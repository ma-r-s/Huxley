// Pseudo-noise for the orb — cheap 1-D smoothed value noise, no deps.
// Uses a seeded permutation table + Perlin-style fade + gradient.

function makeNoise(seed: number) {
  const p = new Uint8Array(512);
  let s = seed * 9301 + 49297;
  const rand = () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
  const perm = Array.from({ length: 256 }, (_: unknown, i: number) => i);
  for (let i = 255; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    const tmp = perm[i];
    perm[i] = perm[j] ?? 0;
    perm[j] = tmp ?? 0;
  }
  for (let i = 0; i < 512; i++) p[i] = perm[i & 255] ?? 0;

  const fade = (t: number) => t * t * t * (t * (t * 6 - 15) + 10);
  const lerp = (a: number, b: number, t: number) => a + t * (b - a);
  const grad = (h: number, x: number) => ((h & 1) === 0 ? x : -x);

  return (x: number, y: number): number => {
    const X = Math.floor(x) & 255;
    const Y = Math.floor(y) & 255;
    const xf = x - Math.floor(x);
    const yf = y - Math.floor(y);
    const u = fade(xf);
    const v = fade(yf);
    const aa = p[(p[X] ?? 0) + Y] ?? 0;
    const ab = p[(p[X] ?? 0) + Y + 1] ?? 0;
    const ba = p[(p[X + 1] ?? 0) + Y] ?? 0;
    const bb = p[(p[X + 1] ?? 0) + Y + 1] ?? 0;
    const x1 = lerp(grad(aa, xf), grad(ba, xf - 1), u);
    const x2 = lerp(grad(ab, xf), grad(bb, xf - 1), u);
    return lerp(x1, x2, v); // ~[-0.7, 0.7]
  };
}

export const NOISE_A = makeNoise(7);
export const NOISE_B = makeNoise(31);
export const NOISE_C = makeNoise(53);
