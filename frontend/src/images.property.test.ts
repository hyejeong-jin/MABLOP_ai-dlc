import { describe, it, expect } from "vitest";
import { scaledDimensions } from "./images";

// Feature: mablop-mvp, Property 17 — Validates: Requirements 7.3
// For any input dims, resize bounds the largest side to the configured max,
// never upscales, and preserves aspect (within rounding).
//
// NOTE: resizeImage's canvas byte-budget path (Image decode + canvas.toBlob)
// is not exercisable under jsdom, so we exercise the pure deterministic core:
// scaledDimensions. fast-check is not a dep, so we use a seeded PRNG loop.

// Mulberry32 — tiny seeded PRNG for reproducible iterations.
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

describe("Property 17: scaledDimensions bounds output", () => {
  it("holds across many random (w, h, max) triples", () => {
    const rand = mulberry32(0x1a2b3c4d);
    const randInt = (lo: number, hi: number) =>
      lo + Math.floor(rand() * (hi - lo + 1));

    for (let i = 0; i < 200; i++) {
      const width = randInt(1, 10000);
      const height = randInt(1, 10000);
      const maxDimension = randInt(1, 4000);

      const out = scaledDimensions(width, height, maxDimension);
      const ctx = `w=${width} h=${height} max=${maxDimension} -> ${out.width}x${out.height}`;

      // dims always >= 1
      expect(out.width, ctx).toBeGreaterThanOrEqual(1);
      expect(out.height, ctx).toBeGreaterThanOrEqual(1);

      // never upscales
      expect(out.width, ctx).toBeLessThanOrEqual(width);
      expect(out.height, ctx).toBeLessThanOrEqual(height);

      const longestIn = Math.max(width, height);
      if (longestIn > maxDimension) {
        // largest side bounded by max (rounding can't push above)
        expect(Math.max(out.width, out.height), ctx).toBeLessThanOrEqual(
          maxDimension,
        );
        // aspect preserved: out ≈ dim*ratio, off only by rounding (<=0.5)
        // or by the clamp-to-1 floor (when dim*ratio < 1).
        const ratio = maxDimension / longestIn;
        const idealW = width * ratio;
        const idealH = height * ratio;
        expect(Math.abs(out.width - idealW), ctx).toBeLessThanOrEqual(
          Math.max(0.5, 1 - idealW),
        );
        expect(Math.abs(out.height - idealH), ctx).toBeLessThanOrEqual(
          Math.max(0.5, 1 - idealH),
        );
      } else {
        // within bounds: untouched
        expect(out, ctx).toEqual({ width, height });
      }
    }
  });
});
