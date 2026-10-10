import AVFoundation

/// Converts microphone buffers, in whatever rate and format the hardware
/// gives (48 kHz Float32 on current iPhones and the simulator), to the wire
/// format: PCM16 mono 24 kHz little-endian.
///
/// One instance per press: the converter's filter state carries across
/// buffers, so consecutive chunks join without clicks, and `flush()` at the
/// end releases the last few ms the converter holds back. If the input format
/// changes mid-press (a headset plugged in), it rebuilds the converter for
/// the new format. It also keeps the per-press numbers the plugin logs on
/// release (frames in/out, peak), which tell a silent mic from a broken
/// conversion.
///
/// No Capacitor dependency, so `bun run ios:test` compiles it standalone.
final class PCM16Resampler {
    static let outputFormat = AVAudioFormat(
        commonFormat: .pcmFormatInt16, sampleRate: 24_000, channels: 1, interleaved: true)!

    private(set) var inputFormat: AVAudioFormat
    private var converter: AVAudioConverter
    // One conversion at a time: the tap thread converts, the audio queue
    // flushes at the end of the press.
    private let conversion = NSLock()
    private var finished = false
    // Guards the stats and `inputFormat`, read from the audio queue.
    private let lock = NSLock()
    private var formatChanges = 0
    private var chunks = 0
    private var inFrames = 0
    private var outFrames = 0
    private var peak = 0

    init?(from inputFormat: AVAudioFormat) {
        guard inputFormat.sampleRate > 0,
              let converter = AVAudioConverter(from: inputFormat, to: Self.outputFormat)
        else { return nil }
        self.inputFormat = inputFormat
        self.converter = converter
    }

    /// One hardware buffer in, PCM16 bytes out; nil when nothing came out.
    func convert(_ buffer: AVAudioPCMBuffer) -> Data? {
        conversion.lock()
        defer { conversion.unlock() }
        guard !finished else { return nil }
        var data = Data()
        if buffer.format != inputFormat {
            // Release what the old converter still holds, then switch.
            if let tail = run(nil) { data.append(tail) }
            guard let fresh = AVAudioConverter(from: buffer.format, to: Self.outputFormat)
            else { return data.isEmpty ? nil : data }
            lock.lock()
            inputFormat = buffer.format
            converter = fresh
            formatChanges += 1
            lock.unlock()
        }
        if let body = run(buffer) { data.append(body) }
        return data.isEmpty ? nil : data
    }

    /// End of the press. The converter holds back its last few ms (~15 ms
    /// at 48 kHz: its filter wants input past them); this releases them.
    /// Nothing converts after a flush.
    func flush() -> Data? {
        conversion.lock()
        defer { conversion.unlock() }
        guard !finished else { return nil }
        finished = true
        return run(nil)
    }

    /// Convert one buffer, or with nil, drain the converter.
    private func run(_ input: AVAudioPCMBuffer?) -> Data? {
        let frames = input.map {
            Double($0.frameLength) * Self.outputFormat.sampleRate / $0.format.sampleRate
        } ?? 2048
        guard let out = AVAudioPCMBuffer(
            pcmFormat: Self.outputFormat, frameCapacity: AVAudioFrameCount(frames) + 32)
        else { return nil }
        // Hand over the buffer once, then "no more for now" so the converter
        // keeps its state for the next call; with no buffer, "end of stream"
        // so it releases what it held back.
        var fed = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if let input, !fed {
                fed = true
                status.pointee = .haveData
                return input
            }
            status.pointee = input == nil ? .endOfStream : .noDataNow
            return nil
        }
        if let input {
            lock.lock()
            chunks += 1
            inFrames += Int(input.frameLength)
            lock.unlock()
        }
        guard error == nil, out.frameLength > 0, let samples = out.int16ChannelData?[0]
        else { return nil }
        var chunkPeak = 0
        for i in 0..<Int(out.frameLength) { chunkPeak = max(chunkPeak, abs(Int(samples[i]))) }
        lock.lock()
        outFrames += Int(out.frameLength)
        peak = max(peak, chunkPeak)
        lock.unlock()
        return Data(bytes: samples, count: Int(out.frameLength) * 2)
    }

    /// Per-press summary for the `native_capture_summary` log line.
    var stats: [String: Any] {
        lock.lock()
        defer { lock.unlock() }
        return [
            "in_rate": inputFormat.sampleRate,
            "in_channels": inputFormat.channelCount,
            "chunks": chunks,
            "in_frames": inFrames,
            "out_frames": outFrames,
            "peak": peak,
            "format_changes": formatChanges,
        ]
    }
}
