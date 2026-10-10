import AVFoundation

/// Converts microphone buffers, in whatever rate and format the hardware
/// gives (48 kHz Float32 on current iPhones and the simulator), to the wire
/// format: PCM16 mono 24 kHz little-endian.
///
/// One instance per press: the converter's filter state carries across
/// buffers, so consecutive chunks join without clicks. It also keeps the
/// per-press numbers the plugin logs on release (frames in/out, peak), which
/// tell a silent mic from a broken conversion.
///
/// No Capacitor dependency, so `bun run ios:test` compiles it standalone.
final class PCM16Resampler {
    static let outputFormat = AVAudioFormat(
        commonFormat: .pcmFormatInt16, sampleRate: 24_000, channels: 1, interleaved: true)!

    let inputFormat: AVAudioFormat
    private let converter: AVAudioConverter
    private let lock = NSLock()
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
        let ratio = Self.outputFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 32
        guard let out = AVAudioPCMBuffer(pcmFormat: Self.outputFormat, frameCapacity: capacity)
        else { return nil }
        // Hand over this buffer once, then report "no more for now" so the
        // converter keeps its state for the next call instead of flushing.
        var fed = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if fed {
                status.pointee = .noDataNow
                return nil
            }
            fed = true
            status.pointee = .haveData
            return buffer
        }
        guard error == nil, out.frameLength > 0, let samples = out.int16ChannelData?[0]
        else { return nil }
        var chunkPeak = 0
        for i in 0..<Int(out.frameLength) { chunkPeak = max(chunkPeak, abs(Int(samples[i]))) }
        lock.lock()
        chunks += 1
        inFrames += Int(buffer.frameLength)
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
        ]
    }
}
