import Foundation

/// Band-limited resampling for speech: a Blackman-windowed sinc (24 zero crossings a side) with one filter per
/// output phase, cut at 90 % of the lower Nyquist frequency, so what the new rate cannot hold is removed instead
/// of folding back as a whistle. Each filter sums to 1 (a steady level is kept); outside the input is silence.
public func resample(_ samples: [Float], from inRate: Int, to outRate: Int) -> [Float] {
    guard inRate > 0, outRate > 0, inRate != outRate, !samples.isEmpty else { return samples }
    func gcd(_ a: Int, _ b: Int) -> Int { b == 0 ? a : gcd(b, a % b) }
    let divisor = gcd(inRate, outRate)
    let up = outRate / divisor  // output sample n sits at input position n * down / up
    let down = inRate / divisor
    let cutoff = 0.45 * min(1, Double(outRate) / Double(inRate))  // cycles per input sample
    let half = Int((12 / cutoff).rounded(.up))  // input samples a side: 24 zero crossings
    let taps = 2 * half
    var filters = [Float](repeating: 0, count: up * taps)
    for phase in 0..<up {
        let row = (0..<taps).map { j -> Double in
            let t = Double(j - half + 1) - Double(phase) / Double(up)  // distance from the output position
            let x = 2 * cutoff * t
            let sinc = x == 0 ? 1 : sin(.pi * x) / (.pi * x)
            return sinc * (0.42 + 0.5 * cos(.pi * t / Double(half)) + 0.08 * cos(2 * .pi * t / Double(half)))
        }
        let sum = row.reduce(0, +)
        for j in 0..<taps { filters[phase * taps + j] = Float(row[j] / sum) }
    }
    let padded = [Float](repeating: 0, count: half) + samples + [Float](repeating: 0, count: half)
    var out = [Float](repeating: 0, count: (samples.count * up + down - 1) / down)
    padded.withUnsafeBufferPointer { x in
        filters.withUnsafeBufferPointer { h in
            out.withUnsafeMutableBufferPointer { y in
                for n in 0..<y.count {
                    let position = n * down
                    let first = position / up + 1  // input sample (position / up - half + 1), after the padding
                    let row = (position % up) * taps
                    var sum: Float = 0
                    for j in 0..<taps { sum += x[first + j] * h[row + j] }
                    y[n] = sum
                }
            }
        }
    }
    return out
}
