import { describe, it, expect } from "vitest";
import {
  MAX_IMAGES,
  DEFAULT_MAX_DIMENSION,
  DEFAULT_MAX_BYTES,
  capImages,
  scaledDimensions,
} from "./images";

// NOTE: resizeImage's canvas path (Image decode + canvas.toBlob) is not functional
// under jsdom, so it is not exercised here. We test the cap + config + pure scaling
// logic. Property 17 (resize byte/dimension bounds) is covered by task 11.3.

describe("MAX_IMAGES constant (Req 7.4)", () => {
  it("is 10 to match backend cap", () => {
    expect(MAX_IMAGES).toBe(10);
  });
});

describe("config defaults", () => {
  it("byte budget matches backend presign maxSize (2 MiB)", () => {
    expect(DEFAULT_MAX_BYTES).toBe(2 * 1024 * 1024);
  });
  it("has a sensible max dimension", () => {
    expect(DEFAULT_MAX_DIMENSION).toBe(1600);
  });
});

describe("capImages (Req 7.4)", () => {
  it("returns all when at or under the cap", () => {
    const five = [1, 2, 3, 4, 5];
    expect(capImages(five)).toEqual(five);
    const ten = Array.from({ length: 10 }, (_, i) => i);
    expect(capImages(ten)).toHaveLength(10);
  });

  it("never returns more than MAX_IMAGES", () => {
    const twenty = Array.from({ length: 20 }, (_, i) => i);
    const capped = capImages(twenty);
    expect(capped).toHaveLength(MAX_IMAGES);
    expect(capped).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
  });

  it("handles empty input", () => {
    expect(capImages([])).toEqual([]);
  });
});

describe("scaledDimensions", () => {
  it("does not upscale when within bounds", () => {
    expect(scaledDimensions(800, 600, 1600)).toEqual({ width: 800, height: 600 });
  });

  it("scales largest side down to the max, preserving aspect", () => {
    const out = scaledDimensions(3200, 1600, 1600);
    expect(Math.max(out.width, out.height)).toBeLessThanOrEqual(1600);
    expect(out).toEqual({ width: 1600, height: 800 });
  });

  it("scales a tall image by its height", () => {
    const out = scaledDimensions(1000, 4000, 1600);
    expect(Math.max(out.width, out.height)).toBeLessThanOrEqual(1600);
    expect(out).toEqual({ width: 400, height: 1600 });
  });

  it("clamps to at least 1px", () => {
    const out = scaledDimensions(10000, 1, 100);
    expect(out.width).toBeGreaterThanOrEqual(1);
    expect(out.height).toBeGreaterThanOrEqual(1);
  });
});
