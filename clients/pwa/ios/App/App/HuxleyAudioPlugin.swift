import AVFoundation
import Capacitor
import Foundation
import UIKit

/// Native audio for the Huxley app: mic capture and voice playback through
/// AVAudioEngine in the app's own process.
///
/// Why not the web view's audio: WKWebView renders audio in a separate
/// WebKit helper process with its own audio session. iOS treats that as
/// audio from a non-foreground process (a live activity in the Dynamic
/// Island while Huxley speaks), and the app can't control its route
/// (WebKit's play-and-record sent replies to the earpiece).
///
/// Lifecycle, chosen for press latency: the audio session stays active while
/// the app is in the foreground, and one capture engine is built once and
/// only started and stopped per press, so a press costs an engine start
/// (~10 ms) instead of a session activation plus an engine build (~160 ms on
/// an iPhone 15 Pro). In the background everything stops and the session is
/// released so other apps' audio can resume.
///
/// Wire format matches the server: PCM16 mono 24 kHz, base64.
/// JS side: src/lib/audio/native.ts.
@objc(HuxleyAudioPlugin)
public class HuxleyAudioPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "HuxleyAudioPlugin"
    public let jsName = "HuxleyAudio"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "startCapture", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "stopCapture", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "play", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "stopPlayback", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "playEffect", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "setLoopingEffect", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "setVolume", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "reset", returnType: CAPPluginReturnPromise),
    ]

    private let queue = DispatchQueue(label: "huxley.audio")

    // Playback: three nodes so they never cut each other. `voice` is the
    // reply queue; `tone` the one-shot UI sounds (a press must not cut its
    // own ready tone when it clears the reply); `loop` the thinking pulse.
    // Rebuilt only if the iOS audio daemon restarts (media services reset).
    private var playEngine = AVAudioEngine()
    private var voice = AVAudioPlayerNode()
    private var tone = AVAudioPlayerNode()
    private var loop = AVAudioPlayerNode()
    private var volume: Float = 1
    private let floatFormat = AVAudioFormat(
        commonFormat: .pcmFormatFloat32, sampleRate: 24_000, channels: 1, interleaved: false)!
    private var pendingBuffers = 0
    private var generation = 0
    private var loopActive = false
    // Sequence number of the latest chunk JS handed us (bridge calls arrive
    // in order). `playbackIdle` carries it so JS can drop an idle that a
    // newer chunk has overtaken.
    private var lastSeq = 0
    // When the scheduled voice audio should have finished, for the watchdog.
    private var playheadEnd = DispatchTime.now()
    private var watchdog: DispatchWorkItem?
    private var pauseWork: DispatchWorkItem?

    // Session.
    private var foreground = true
    private var sessionActive = false

    // Capture: one engine, tap installed once, started per press.
    private var captureEngine: AVAudioEngine?
    private let sink = CaptureSink()
    private var pendingStop: CAPPluginCall?
    private var stopFallback: DispatchWorkItem?

    override public func load() {
        queue.sync { buildPlayback() }
        queue.async { [weak self] in self?.warmUp("load") }
        let center = NotificationCenter.default
        center.addObserver(
            self, selector: #selector(handleInterruption),
            name: AVAudioSession.interruptionNotification, object: nil)
        center.addObserver(
            self, selector: #selector(handleMediaServicesReset),
            name: AVAudioSession.mediaServicesWereResetNotification, object: nil)
        center.addObserver(
            self, selector: #selector(appDidBecomeActive),
            name: UIApplication.didBecomeActiveNotification, object: nil)
        center.addObserver(
            self, selector: #selector(appDidEnterBackground),
            name: UIApplication.didEnterBackgroundNotification, object: nil)
    }

    private func buildPlayback() {
        NotificationCenter.default.removeObserver(
            self, name: .AVAudioEngineConfigurationChange, object: playEngine)
        playEngine = AVAudioEngine()
        voice = AVAudioPlayerNode()
        tone = AVAudioPlayerNode()
        loop = AVAudioPlayerNode()
        for node in [voice, tone, loop] {
            playEngine.attach(node)
            playEngine.connect(node, to: playEngine.mainMixerNode, format: floatFormat)
            node.volume = volume
        }
        playEngine.prepare()
        NotificationCenter.default.addObserver(
            self, selector: #selector(handlePlayEngineReset),
            name: .AVAudioEngineConfigurationChange, object: playEngine)
    }

    private func emit(_ name: String, _ data: [String: Any] = [:]) {
        var payload = data
        payload["name"] = name
        notifyListeners("event", data: payload)
    }

    private static func ms(since start: DispatchTime) -> Int {
        Int((DispatchTime.now().uptimeNanoseconds - start.uptimeNanoseconds) / 1_000_000)
    }

    // MARK: - Session and app lifecycle

    /// play-and-record + defaultToSpeaker: the main speaker (not the
    /// earpiece), the silent switch doesn't mute it, and no category switch
    /// is needed between talking and listening. Options are checked too: a
    /// play-and-record session without defaultToSpeaker routes replies to
    /// the earpiece.
    private func activateSession() throws {
        let session = AVAudioSession.sharedInstance()
        if session.category != .playAndRecord || session.mode != .default
            || !session.categoryOptions.contains(.defaultToSpeaker)
        {
            try session.setCategory(
                .playAndRecord, mode: .default,
                options: [.defaultToSpeaker, .allowBluetoothA2DP])
        }
        if !sessionActive {
            try session.setActive(true)
            sessionActive = true
        }
    }

    /// Foreground: session on and capture engine built, so the first press
    /// is as fast as the rest.
    private func warmUp(_ reason: String) {
        let started = DispatchTime.now()
        do {
            try activateSession()
            if AVAudioApplication.shared.recordPermission == .granted {
                try prepareCapture()
            }
            emit("native_audio_warm", ["reason": reason, "ms": Self.ms(since: started)])
            if reason != "load" && reason != "foreground" {
                // A call (skill_continuous) whose mic iOS stopped can reopen
                // it now; the foreground case is covered by the page itself.
                notifyListeners("captureAvailable", data: ["reason": reason])
            }
        } catch {
            sessionActive = false
            emit("native_audio_warm_failed", ["reason": reason, "error": "\(error)"])
        }
    }

    /// Background only: release the session once nothing is playing or
    /// recording, so other apps' audio can resume.
    private func deactivateIfIdle() {
        guard !foreground, sessionActive, pendingBuffers == 0, !loopActive,
              captureEngine?.isRunning != true else { return }
        playEngine.pause()
        teardownCapture()
        do {
            try AVAudioSession.sharedInstance().setActive(
                false, options: .notifyOthersOnDeactivation)
            sessionActive = false
        } catch {
            emit("native_audio_deactivate_failed", ["error": "\(error)"])
        }
    }

    @objc private func appDidBecomeActive() {
        queue.async { [weak self] in
            guard let self else { return }
            self.foreground = true
            self.warmUp("foreground")
        }
    }

    @objc private func appDidEnterBackground() {
        // No background audio mode: iOS suspends the app shortly anyway.
        // Stop cleanly now so nothing resumes stale when it comes back.
        queue.async { [weak self] in
            guard let self else { return }
            self.foreground = false
            self.haltAll(reason: "background")
            self.deactivateIfIdle()
        }
    }

    /// Something else took the audio (a call, Siri, an alarm) or the app
    /// left the screen: drop the reply instead of resuming it later
    /// mid-sentence, and tell JS the mic is no longer recording.
    private func haltAll(reason: String) {
        let hadVoice = pendingBuffers > 0
        clearVoice()
        if hadVoice { notifyIdle() }
        loop.stop()
        loopActive = false
        tone.stop()
        if captureEngine?.isRunning == true || sink.press != nil {
            finishCapture(cutShort: true)
            notifyListeners("captureEnded", data: ["reason": reason])
        }
        pauseWork?.cancel()
        playEngine.pause()
    }

    @objc private func handleInterruption(_ note: Notification) {
        guard let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
              let type = AVAudioSession.InterruptionType(rawValue: raw) else { return }
        queue.async { [weak self] in
            guard let self else { return }
            self.emit("native_audio_interruption", ["type": type == .began ? "began" : "ended"])
            if type == .began {
                self.sessionActive = false
                self.haltAll(reason: "interruption")
            } else if self.foreground {
                self.warmUp("interruption_ended")
            }
        }
    }

    @objc private func handleMediaServicesReset(_ note: Notification) {
        // The audio daemon restarted; every engine is invalid. Rare. Both
        // engines are rebuilt.
        queue.async { [weak self] in
            guard let self else { return }
            self.emit("native_media_services_reset")
            self.sessionActive = false
            self.haltAll(reason: "media_services_reset")
            self.teardownCapture()
            self.buildPlayback()
            if self.foreground { self.warmUp("media_services_reset") }
        }
    }

    // MARK: - Playback

    private func ensurePlaying() throws {
        try activateSession()
        pauseWork?.cancel()
        if !playEngine.isRunning { try playEngine.start() }
        for node in [voice, tone, loop] where !node.isPlaying { node.play() }
    }

    /// Pause the output engine a few seconds after the last sound, so an
    /// idle app isn't running audio I/O. The session stays active in the
    /// foreground; restarting the engine for the next sound is cheap.
    private func schedulePauseIfIdle() {
        pauseWork?.cancel()
        guard pendingBuffers == 0, !loopActive else { return }
        let work = DispatchWorkItem { [weak self] in
            guard let self, self.pendingBuffers == 0, !self.loopActive else { return }
            self.playEngine.pause()
            self.deactivateIfIdle()
        }
        pauseWork = work
        queue.asyncAfter(deadline: .now() + 3, execute: work)
    }

    private func floatBuffer(fromBase64 b64: String) -> AVAudioPCMBuffer? {
        guard let data = Data(base64Encoded: b64), data.count >= 2 else { return nil }
        let frames = AVAudioFrameCount(data.count / 2)
        guard let buffer = AVAudioPCMBuffer(pcmFormat: floatFormat, frameCapacity: frames),
              let out = buffer.floatChannelData?[0] else { return nil }
        buffer.frameLength = frames
        data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            let samples = raw.bindMemory(to: Int16.self)
            for i in 0..<Int(frames) { out[i] = Float(Int16(littleEndian: samples[i])) / 32768.0 }
        }
        return buffer
    }

    private func notifyIdle() {
        notifyListeners("playbackIdle", data: ["seq": lastSeq])
    }

    /// Drop everything queued on the voice node. Completion handlers of the
    /// dropped buffers see a stale generation and do nothing.
    private func clearVoice() {
        generation += 1
        voice.stop()
        pendingBuffers = 0
        watchdog?.cancel()
        playheadEnd = DispatchTime.now()
        if playEngine.isRunning { voice.play() }
    }

    @objc func play(_ call: CAPPluginCall) {
        let seq = call.getInt("seq") ?? 0
        let b64 = call.getString("data")
        queue.async { [weak self] in
            guard let self else { return }
            self.lastSeq = seq
            guard let b64, let buffer = self.floatBuffer(fromBase64: b64) else {
                // JS marked itself busy when it sent this; don't leave it
                // waiting for an idle that would never come.
                if self.pendingBuffers == 0 { self.notifyIdle() }
                call.reject("bad audio chunk")
                return
            }
            do {
                try self.ensurePlaying()
            } catch {
                self.sessionActive = false
                if self.pendingBuffers == 0 { self.notifyIdle() }
                self.emit("native_play_failed", ["error": "\(error)"])
                call.reject("playback failed: \(error)")
                return
            }
            self.pendingBuffers += 1
            let gen = self.generation
            self.voice.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
                self?.queue.async {
                    guard let self, gen == self.generation else { return }
                    self.pendingBuffers -= 1
                    if self.pendingBuffers == 0 {
                        self.watchdog?.cancel()
                        self.notifyIdle()
                        self.schedulePauseIfIdle()
                    }
                }
            }
            self.armWatchdog(adding: Double(buffer.frameLength) / self.floatFormat.sampleRate)
            call.resolve()
        }
    }

    /// If the engine drops scheduled buffers without calling their
    /// completions (a route change or a system stop can), `pendingBuffers`
    /// never reaches 0 and JS waits forever for the reply to end. Force the
    /// idle a second after the audio should have finished.
    private func armWatchdog(adding seconds: Double) {
        let now = DispatchTime.now()
        let start = playheadEnd > now ? playheadEnd : now
        playheadEnd = start + seconds
        watchdog?.cancel()
        let gen = generation
        let work = DispatchWorkItem { [weak self] in
            guard let self, gen == self.generation, self.pendingBuffers > 0 else { return }
            self.emit("native_playback_watchdog", ["pending": self.pendingBuffers])
            self.clearVoice()
            self.notifyIdle()
            self.schedulePauseIfIdle()
        }
        watchdog = work
        queue.asyncAfter(deadline: playheadEnd + 1, execute: work)
    }

    /// Clears the reply and the thinking pulse; a one-shot tone (the ready
    /// tone of the press that caused this) plays out. JS settles its own
    /// state synchronously, so no idle is sent back.
    @objc func stopPlayback(_ call: CAPPluginCall) {
        queue.async { [weak self] in
            guard let self else { return }
            self.clearVoice()
            self.loop.stop()
            self.loopActive = false
            if self.playEngine.isRunning { self.loop.play() }
            self.schedulePauseIfIdle()
            call.resolve()
        }
    }

    /// One-shot UI sound (ready / error tones).
    @objc func playEffect(_ call: CAPPluginCall) {
        guard let b64 = call.getString("data"), let buffer = floatBuffer(fromBase64: b64) else {
            call.reject("bad audio chunk")
            return
        }
        queue.async { [weak self] in
            guard let self else { return }
            do { try self.ensurePlaying() } catch {
                self.sessionActive = false
                call.reject("effect failed: \(error)")
                return
            }
            self.tone.scheduleBuffer(buffer, at: nil, options: .interrupts)
            self.schedulePauseIfIdle()
            call.resolve()
        }
    }

    /// Looping UI sound (the thinking pulse). No `data` stops it.
    @objc func setLoopingEffect(_ call: CAPPluginCall) {
        let b64 = call.getString("data")
        queue.async { [weak self] in
            guard let self else { return }
            if let b64, let buffer = self.floatBuffer(fromBase64: b64) {
                do { try self.ensurePlaying() } catch {
                    self.sessionActive = false
                    call.reject("loop failed: \(error)")
                    return
                }
                self.loop.scheduleBuffer(buffer, at: nil, options: [.loops, .interrupts])
                self.loopActive = true
            } else {
                self.loop.stop()
                self.loopActive = false
                if self.playEngine.isRunning { self.loop.play() }
                self.schedulePauseIfIdle()
            }
            call.resolve()
        }
    }

    @objc func setVolume(_ call: CAPPluginCall) {
        let level = Float(max(0, min(100, call.getDouble("level") ?? 100))) / 100
        queue.async { [weak self] in
            guard let self else { return }
            self.volume = level
            for node in [self.voice, self.tone, self.loop] { node.volume = level }
            call.resolve()
        }
    }

    @objc private func handlePlayEngineReset(_ note: Notification) {
        // A route change (headphones, Bluetooth) stopped the engine. Restart
        // it if anything is playing; if the change dropped the queued
        // buffers, the watchdog ends the reply.
        queue.async { [weak self] in
            guard let self else { return }
            self.emit("native_audio_engine_reset", [
                "pending": self.pendingBuffers, "loop": self.loopActive,
            ])
            guard self.pendingBuffers > 0 || self.loopActive else { return }
            // The reply paused while the route switched (Bluetooth can take
            // over a second): push the watchdog back by what's left.
            let remaining = self.playheadEnd > DispatchTime.now()
                ? Double(self.playheadEnd.uptimeNanoseconds - DispatchTime.now().uptimeNanoseconds) / 1e9
                : 0
            do { try self.ensurePlaying() } catch {
                self.sessionActive = false
                self.emit("native_audio_restart_failed", ["error": "\(error)"])
            }
            if self.pendingBuffers > 0 {
                self.playheadEnd = DispatchTime.now()
                self.armWatchdog(adding: remaining)
            }
        }
    }

    // MARK: - Capture

    /// Build the capture engine once. The tap stays installed; a press only
    /// starts the engine. The input runs only between start and stop, so
    /// the mic is off between presses. Not prepare()d ahead: start() does
    /// it in a few ms, and a prepared input unit is not worth the doubt
    /// about whether it holds the mic.
    @discardableResult
    private func prepareCapture() throws -> AVAudioEngine {
        if let engine = captureEngine { return engine }
        let engine = AVAudioEngine()
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            throw CaptureError.noInput
        }
        // format: nil = whatever the input delivers when the tap runs. A
        // fixed format raises an uncatchable exception if the hardware rate
        // changes between now and start; the resampler adapts instead.
        // 100 ms is the smallest tap buffer AVAudioEngine supports.
        let frames = AVAudioFrameCount(format.sampleRate / 10)
        input.installTap(onBus: 0, bufferSize: frames, format: nil) { [weak self] buffer, _ in
            self?.tapped(buffer)
        }
        NotificationCenter.default.addObserver(
            self, selector: #selector(handleCaptureReset),
            name: .AVAudioEngineConfigurationChange, object: engine)
        captureEngine = engine
        return engine
    }

    private func teardownCapture() {
        guard let engine = captureEngine else { return }
        NotificationCenter.default.removeObserver(
            self, name: .AVAudioEngineConfigurationChange, object: engine)
        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        captureEngine = nil
    }

    /// Tap thread. Converts and forwards the block; if a stop is waiting,
    /// this block was the one holding the last of the speech, so the stop
    /// can finish now (on the queue, after this frame is on its way).
    private func tapped(_ buffer: AVAudioPCMBuffer) {
        guard let press = sink.press else { return }
        if let data = press.resampler.convert(buffer) {
            notifyListeners("frame", data: ["data": data.base64EncodedString()])
        }
        if press.stopRequested {
            queue.async { [weak self] in
                guard let self, self.sink.press === press else { return }
                self.finishCapture(cutShort: false)
            }
        }
    }

    @objc func startCapture(_ call: CAPPluginCall) {
        switch AVAudioApplication.shared.recordPermission {
        case .granted:
            queue.async { [weak self] in self?.beginCapture(call) }
        case .undetermined:
            AVAudioApplication.requestRecordPermission { [weak self] granted in
                guard let self else { return }
                if granted {
                    self.queue.async { self.beginCapture(call) }
                } else {
                    call.reject("Microphone permission denied", "NotAllowedError")
                }
            }
        default:
            call.reject("Microphone permission denied", "NotAllowedError")
        }
    }

    private func beginCapture(_ call: CAPPluginCall) {
        // A press while the previous one is still draining: finish that one
        // now (its stop resolves, so its release reaches the server first).
        if sink.press != nil { finishCapture(cutShort: true) }
        let started = DispatchTime.now()
        let warmSession = sessionActive
        let warmEngine = captureEngine != nil
        var retried = false
        do {
            try activateSession()
            let sessionMs = Self.ms(since: started)
            var engine = try prepareCapture()
            let buildMs = Self.ms(since: started) - sessionMs
            let startAt = DispatchTime.now()
            var format = engine.inputNode.outputFormat(forBus: 0)
            sink.press = try Press(format: format)
            do {
                try engine.start()
            } catch {
                // The engine was built for a route that has since changed
                // (headset unplugged while idle). Rebuild once and retry.
                emit("native_capture_retry", ["error": "\(error)"])
                retried = true
                teardownCapture()
                engine = try prepareCapture()
                format = engine.inputNode.outputFormat(forBus: 0)
                sink.press = try Press(format: format)
                try engine.start()
            }
            call.resolve([
                "ms": Self.ms(since: started),
                "session_ms": sessionMs,
                "build_ms": buildMs,
                "start_ms": Self.ms(since: startAt),
                "warm_session": warmSession,
                "warm_engine": warmEngine,
                "retried": retried,
                "inputRate": format.sampleRate,
            ])
        } catch {
            sink.press = nil
            sessionActive = false
            teardownCapture()
            if error as? CaptureError == .noInput {
                call.reject("no usable microphone input", "NotFoundError")
            } else {
                call.reject("capture failed: \(error)", "CaptureError")
            }
        }
    }

    /// Release: keep recording until the tap hands over its next block.
    /// That block holds the audio up to and past the release (the tap
    /// delivers 100 ms at a time; stopping at once would drop the partial
    /// block, often the end of the last word). Resolves once that block has
    /// been sent to JS, which then tells the server the press ended.
    @objc func stopCapture(_ call: CAPPluginCall) {
        queue.async { [weak self] in
            guard let self else { return }
            guard let press = self.sink.press, self.captureEngine?.isRunning == true else {
                if self.sink.press != nil { self.finishCapture(cutShort: true) }
                call.resolve()
                return
            }
            self.pendingStop?.resolve()
            self.pendingStop = call
            press.stopRequested = true
            // The tap normally delivers within 100 ms; don't hang the
            // release on a tap that has stopped (engine died, route change).
            let fallback = DispatchWorkItem { [weak self] in
                guard let self, self.sink.press === press else { return }
                self.finishCapture(cutShort: true)
            }
            self.stopFallback = fallback
            self.queue.asyncAfter(deadline: .now() + .milliseconds(250), execute: fallback)
        }
    }

    /// Stop the engine, log the press, resolve a waiting stop.
    private func finishCapture(cutShort: Bool) {
        stopFallback?.cancel()
        stopFallback = nil
        if let engine = captureEngine {
            engine.stop()
            // Drop whatever the tap had gathered after the last delivered
            // block, so it can't open the next press.
            engine.reset()
        }
        if let press = sink.press {
            sink.press = nil
            var data = press.resampler.stats
            data["drained"] = !cutShort
            emit("native_capture_summary", data)
        }
        pendingStop?.resolve()
        pendingStop = nil
        deactivateIfIdle()
    }

    /// The page (re)loaded: whatever the previous page left running is
    /// orphaned (its listeners are gone). Stop it and start the sequence
    /// over, or every idle would carry the old page's numbers.
    @objc func reset(_ call: CAPPluginCall) {
        queue.async { [weak self] in
            guard let self else { return }
            if self.sink.press != nil { self.finishCapture(cutShort: true) }
            self.clearVoice()
            self.loop.stop()
            self.loopActive = false
            self.tone.stop()
            if self.playEngine.isRunning {
                self.loop.play()
                self.tone.play()
            }
            self.lastSeq = 0
            self.schedulePauseIfIdle()
            call.resolve()
        }
    }

    @objc private func handleCaptureReset(_ note: Notification) {
        // The input route or format changed (headset plugged in, Bluetooth).
        // The engine has stopped. Rebuild it; if a press or a call was
        // recording, carry on with the new input.
        queue.async { [weak self] in
            guard let self, let engine = note.object as? AVAudioEngine,
                  engine === self.captureEngine else { return }
            let press = self.sink.press
            self.emit("native_capture_reset", ["recording": press != nil])
            self.teardownCapture()
            guard let press else { return }
            do {
                // Same Press: its resampler adapts to the new input format.
                try self.prepareCapture().start()
            } catch {
                self.finishCapture(cutShort: true)
                self.notifyListeners("captureEnded", data: ["reason": "route_change"])
            }
        }
    }
}

private enum CaptureError: Error {
    case noInput
}

/// One press being recorded.
private final class Press {
    let resampler: PCM16Resampler
    private let lock = NSLock()
    private var stopFlag = false

    init(format: AVAudioFormat) throws {
        guard let resampler = PCM16Resampler(from: format) else { throw CaptureError.noInput }
        self.resampler = resampler
    }

    /// Set on the audio queue, read on the tap thread.
    var stopRequested: Bool {
        get { lock.lock(); defer { lock.unlock() }; return stopFlag }
        set { lock.lock(); stopFlag = newValue; lock.unlock() }
    }
}

/// The press currently recording, shared between the audio queue (which
/// sets it) and the input tap's thread (which reads it).
private final class CaptureSink {
    private let lock = NSLock()
    private var current: Press?

    var press: Press? {
        get { lock.lock(); defer { lock.unlock() }; return current }
        set { lock.lock(); current = newValue; lock.unlock() }
    }
}
