import AVFoundation

// Checks PCM16Resampler (ios/App/App/PCM16Resampler.swift) without a device:
// feed a 1 kHz sine in the hardware formats an iPhone can hand the mic tap,
// chunked the way the tap delivers it, and check the 24 kHz PCM16 output
// keeps its length, pitch, level, and continuity across chunk boundaries.
//
// Run: bun run ios:test (from clients/pwa)

struct Case {
    let name: String
    let rate: Double
    let channels: AVAudioChannelCount
    let chunk: AVAudioFrameCount
}

let cases = [
    Case(name: "iPhone/simulator mic, 48 kHz", rate: 48_000, channels: 1, chunk: 4800),
    Case(name: "48 kHz, small tap buffers", rate: 48_000, channels: 1, chunk: 1024),
    Case(name: "44.1 kHz (non-integer ratio)", rate: 44_100, channels: 1, chunk: 1024),
    Case(name: "16 kHz Bluetooth headset", rate: 16_000, channels: 1, chunk: 1600),
    Case(name: "48 kHz stereo input", rate: 48_000, channels: 2, chunk: 4800),
]

let toneHz = 1000.0
let amplitude: Float = 0.5
let seconds = 2.0

@main
struct ResamplerCheck {
    static func main() {
        var failures = 0
        for c in cases {
            let problems = run(c)
            if problems.isEmpty {
                print("ok    \(c.name)")
            } else {
                failures += 1
                print("FAIL  \(c.name)")
                for p in problems { print("        \(p)") }
            }
        }
        if failures > 0 {
            print("\(failures) of \(cases.count) cases failed")
            exit(1)
        }
        print("all \(cases.count) cases passed")
    }

    static func run(_ c: Case) -> [String] {
        let format = AVAudioFormat(
            commonFormat: .pcmFormatFloat32, sampleRate: c.rate, channels: c.channels,
            interleaved: false)!
        guard let resampler = PCM16Resampler(from: format) else { return ["init returned nil"] }

        var out: [Int16] = []
        let total = Int(c.rate * seconds)
        var pos = 0
        while pos < total {
            let n = min(Int(c.chunk), total - pos)
            let buf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(n))!
            buf.frameLength = AVAudioFrameCount(n)
            for ch in 0..<Int(c.channels) {
                let dst = buf.floatChannelData![ch]
                for i in 0..<n {
                    dst[i] = amplitude * Float(sin(2 * Double.pi * toneHz * Double(pos + i) / c.rate))
                }
            }
            if let data = resampler.convert(buf) {
                data.withUnsafeBytes { raw in
                    out.append(contentsOf: raw.bindMemory(to: Int16.self).map { Int16(littleEndian: $0) })
                }
            }
            pos += n
        }

        var problems: [String] = []
        let expected = Int(24_000 * seconds)
        // A few ms of converter latency is fine; a lost or doubled chunk is not.
        if abs(out.count - expected) > 240 {
            problems.append("length \(out.count), expected about \(expected)")
        }
        // Skip the converter's start-up ramp before measuring.
        let body = Array(out.dropFirst(240))
        guard body.count > 24_000 else { return problems + ["too little output to measure"] }

        var crossings = 0
        for i in 1..<body.count where (body[i - 1] < 0) != (body[i] < 0) { crossings += 1 }
        let hz = Double(crossings) / 2 / (Double(body.count) / 24_000)
        if abs(hz - toneHz) / toneHz > 0.02 {
            problems.append(String(format: "pitch %.0f Hz, expected %.0f (wrong rate)", hz, toneHz))
        }

        let peak = body.map { abs(Int($0)) }.max() ?? 0
        let expectedPeak = Int(amplitude * 32767)
        if abs(peak - expectedPeak) > expectedPeak / 10 {
            problems.append("peak \(peak), expected about \(expectedPeak)")
        }

        // Largest step a clean 1 kHz sine takes at 24 kHz; a dropped or
        // repeated span at a chunk boundary jumps far past it.
        let maxStep = 2 * Double.pi * toneHz / 24_000 * Double(expectedPeak)
        var worst = 0
        for i in 1..<body.count { worst = max(worst, abs(Int(body[i]) - Int(body[i - 1]))) }
        if Double(worst) > maxStep * 1.25 {
            problems.append("discontinuity: step \(worst), clean sine max \(Int(maxStep))")
        }

        let stats = resampler.stats
        if stats["out_frames"] as? Int != out.count {
            problems.append("stats out_frames \(stats["out_frames"] ?? "nil") != \(out.count)")
        }
        return problems
    }
}
