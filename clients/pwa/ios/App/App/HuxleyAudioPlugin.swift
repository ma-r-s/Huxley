import AVFoundation
import Capacitor
import Foundation

/// Native audio for the Huxley app: mic capture and voice playback through
/// AVAudioEngine in the app's own process.
///
/// Why not the web view's audio: WKWebView renders audio in a separate
/// WebKit helper process with its own audio session. iOS treats that as
/// audio from a non-foreground process (a live activity in the Dynamic
/// Island while Huxley speaks), and the app can't control its route
/// (WebKit's play-and-record sent replies to the earpiece).
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
    ]

    private let queue = DispatchQueue(label: "huxley.audio")
    private let playEngine = AVAudioEngine()
    private let voice = AVAudioPlayerNode()
    private let effects = AVAudioPlayerNode()
    private let floatFormat = AVAudioFormat(
        commonFormat: .pcmFormatFloat32, sampleRate: 24_000, channels: 1, interleaved: false)!
    private var captureEngine: AVAudioEngine?
    private var resampler: PCM16Resampler?
    private var pendingBuffers = 0
    private var generation = 0
    private var deactivateWork: DispatchWorkItem?

    override public func load() {
        queue.sync {
            playEngine.attach(voice)
            playEngine.attach(effects)
            playEngine.connect(voice, to: playEngine.mainMixerNode, format: floatFormat)
            playEngine.connect(effects, to: playEngine.mainMixerNode, format: floatFormat)
            playEngine.prepare()
        }
        let center = NotificationCenter.default
        center.addObserver(
            self, selector: #selector(handleInterruption),
            name: AVAudioSession.interruptionNotification, object: nil)
        center.addObserver(
            self, selector: #selector(handleEngineReset),
            name: .AVAudioEngineConfigurationChange, object: playEngine)
    }

    // MARK: - Session

    /// play-and-record + defaultToSpeaker: the main speaker (not the
    /// earpiece), the silent switch doesn't mute it, and no category switch
    /// is needed between talking and listening. Activated on demand and
    /// released when idle so other apps' audio can resume.
    private func activateSession() throws {
        deactivateWork?.cancel()
        deactivateWork = nil
        let session = AVAudioSession.sharedInstance()
        if session.category != .playAndRecord {
            try session.setCategory(
                .playAndRecord, mode: .default,
                options: [.defaultToSpeaker, .allowBluetoothA2DP])
        }
        try session.setActive(true)
    }

    private func scheduleDeactivateIfIdle() {
        guard pendingBuffers == 0, captureEngine == nil else { return }
        deactivateWork?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self, self.pendingBuffers == 0, self.captureEngine == nil else { return }
            self.playEngine.pause()
            try? AVAudioSession.sharedInstance().setActive(
                false, options: .notifyOthersOnDeactivation)
        }
        deactivateWork = work
        queue.asyncAfter(deadline: .now() + 3, execute: work)
    }

    private func ensurePlaying() throws {
        try activateSession()
        if !playEngine.isRunning { try playEngine.start() }
        if !voice.isPlaying { voice.play() }
        if !effects.isPlaying { effects.play() }
    }

    @objc private func handleInterruption(_ note: Notification) {
        guard let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
              let type = AVAudioSession.InterruptionType(rawValue: raw) else { return }
        notifyListeners("event", data: ["name": "native_audio_interruption", "type": type == .began ? "began" : "ended"])
        if type == .ended {
            queue.async { [weak self] in
                guard let self, self.pendingBuffers > 0 else { return }
                try? self.ensurePlaying()
            }
        }
    }

    @objc private func handleEngineReset(_ note: Notification) {
        // Route change (headphones, Bluetooth) stopped the engine.
        queue.async { [weak self] in
            guard let self else { return }
            self.notifyListeners("event", data: ["name": "native_audio_engine_reset"])
            if self.pendingBuffers > 0 { try? self.ensurePlaying() }
        }
    }

    // MARK: - Playback

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

    @objc func play(_ call: CAPPluginCall) {
        guard let b64 = call.getString("data"), let buffer = floatBuffer(fromBase64: b64) else {
            call.reject("bad audio chunk")
            return
        }
        queue.async { [weak self] in
            guard let self else { return }
            do {
                try self.ensurePlaying()
            } catch {
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
                        self.notifyListeners("playbackIdle", data: [:])
                        self.scheduleDeactivateIfIdle()
                    }
                }
            }
            call.resolve()
        }
    }

    @objc func stopPlayback(_ call: CAPPluginCall) {
        queue.async { [weak self] in
            guard let self else { return }
            self.generation += 1
            self.voice.stop()
            self.effects.stop()
            self.pendingBuffers = 0
            if self.playEngine.isRunning {
                self.voice.play()
                self.effects.play()
            }
            self.notifyListeners("playbackIdle", data: [:])
            self.scheduleDeactivateIfIdle()
            call.resolve()
        }
    }

    /// One-shot UI sound (ready / error tones) on its own node, so it never
    /// reorders or cuts the voice queue.
    @objc func playEffect(_ call: CAPPluginCall) {
        guard let b64 = call.getString("data"), let buffer = floatBuffer(fromBase64: b64) else {
            call.reject("bad audio chunk")
            return
        }
        queue.async { [weak self] in
            guard let self else { return }
            do { try self.ensurePlaying() } catch {
                call.reject("effect failed: \(error)")
                return
            }
            self.effects.scheduleBuffer(buffer, at: nil, options: .interrupts)
            self.scheduleDeactivateIfIdle()
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
                    call.reject("loop failed: \(error)")
                    return
                }
                self.effects.scheduleBuffer(buffer, at: nil, options: [.loops, .interrupts])
            } else {
                self.effects.stop()
                if self.playEngine.isRunning { self.effects.play() }
                self.scheduleDeactivateIfIdle()
            }
            call.resolve()
        }
    }

    @objc func setVolume(_ call: CAPPluginCall) {
        let level = Float(max(0, min(100, call.getDouble("level") ?? 100))) / 100
        queue.async { [weak self] in
            self?.voice.volume = level
            self?.effects.volume = level
            call.resolve()
        }
    }

    // MARK: - Capture

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
        if captureEngine != nil {
            call.resolve()
            return
        }
        let started = Date()
        do {
            try activateSession()
            let engine = AVAudioEngine()
            let input = engine.inputNode
            let inFormat = input.outputFormat(forBus: 0)
            guard let resampler = PCM16Resampler(from: inFormat) else {
                call.reject("no usable microphone input", "NotFoundError")
                return
            }
            input.installTap(onBus: 0, bufferSize: 1024, format: inFormat) { [weak self] buffer, _ in
                guard let self, let data = resampler.convert(buffer) else { return }
                self.notifyListeners("frame", data: ["data": data.base64EncodedString()])
            }
            try engine.start()
            captureEngine = engine
            self.resampler = resampler
            call.resolve([
                "ms": Int(Date().timeIntervalSince(started) * 1000),
                "inputRate": inFormat.sampleRate,
            ])
        } catch {
            call.reject("capture failed: \(error)", "CaptureError")
        }
    }

    @objc func stopCapture(_ call: CAPPluginCall) {
        queue.async { [weak self] in
            guard let self else { return }
            if let engine = self.captureEngine {
                engine.inputNode.removeTap(onBus: 0)
                engine.stop()
                self.captureEngine = nil
            }
            if let resampler = self.resampler {
                // One line per press: a flat peak means the mic heard
                // nothing; out_frames far from in_frames x 24k/in_rate means
                // the conversion is broken.
                var data = resampler.stats
                data["name"] = "native_capture_summary"
                self.notifyListeners("event", data: data)
                self.resampler = nil
            }
            self.scheduleDeactivateIfIdle()
            call.resolve()
        }
    }
}
