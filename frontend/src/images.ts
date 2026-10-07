// Client-side image prep (cost control). Native browser APIs only -- no image libs (Req 7.3).
// resize/compress keeps vision + storage cost down; MAX_IMAGES caps per-draft count (Req 7.4).

// 10-image cap (Req 7.4). Matches backend _MAX_IMAGES.
export const MAX_IMAGES = 10;

// Defaults match backend presign maxSize (2 MiB) and a sensible on-screen max dimension.
export const DEFAULT_MAX_DIMENSION = 1600;
export const DEFAULT_MAX_BYTES = 2 * 1024 * 1024;

export interface ResizeOptions {
  maxDimension?: number; // largest side cap, px
  maxBytes?: number; // output byte budget
  mimeType?: string; // output encoding; JPEG by default (small + wide support)
}

// Cap a file list to at most MAX_IMAGES (Req 7.4).
export function capImages<T>(files: readonly T[]): T[] {
  return files.slice(0, MAX_IMAGES);
}

// Scale so the largest dimension <= max, preserving aspect. Never upscales.
export function scaledDimensions(
  width: number,
  height: number,
  maxDimension: number,
): { width: number; height: number } {
  const longest = Math.max(width, height);
  if (longest <= maxDimension) return { width, height };
  const ratio = maxDimension / longest;
  return {
    width: Math.max(1, Math.round(width * ratio)),
    height: Math.max(1, Math.round(height * ratio)),
  };
}

// Decode a File into an HTMLImageElement via an object URL.
function loadImage(file: Blob): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("image decode failed"));
    };
    img.src = url;
  });
}

// canvas.toBlob wrapped as a promise.
function canvasToBlob(
  canvas: HTMLCanvasElement,
  type: string,
  quality: number,
): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error("toBlob returned null"))),
      type,
      quality,
    );
  });
}

// Downscale largest dim <= maxDimension, re-encode under maxBytes by lowering quality.
// Native canvas path -- unavailable in jsdom, so this runs only in a real browser.
export async function resizeImage(
  file: Blob,
  options: ResizeOptions = {},
): Promise<Blob> {
  const maxDimension = options.maxDimension ?? DEFAULT_MAX_DIMENSION;
  const maxBytes = options.maxBytes ?? DEFAULT_MAX_BYTES;
  const mimeType = options.mimeType ?? "image/jpeg";

  const img = await loadImage(file);
  const { width, height } = scaledDimensions(
    img.naturalWidth || img.width,
    img.naturalHeight || img.height,
    maxDimension,
  );

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("2d context unavailable");
  ctx.drawImage(img, 0, 0, width, height);

  // Step quality down until under budget. Last attempt wins even if still over.
  let quality = 0.92;
  let out = await canvasToBlob(canvas, mimeType, quality);
  while (out.size > maxBytes && quality > 0.3) {
    quality -= 0.15;
    out = await canvasToBlob(canvas, mimeType, quality);
  }
  return out;
}
