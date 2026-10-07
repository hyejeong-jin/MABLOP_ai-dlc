// Task 11.5 -- the DraftingView UPLOAD CONTRACT, stated explicitly (Req 3.1, 7.4).
// Design Principle 2: browser PUTs bytes straight to S3; the Lambda only ever sees S3 keys.
// Overlap note: DraftingView.test.tsx already asserts the cap and keys-only payloads via
// exact-shape checks. These two tests re-state the contract as INVARIANTS over N>10 inputs
// and a DEEP no-binary walk, so the guarantee survives future refactors of either payload.
import { render, screen, fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { DraftingView } from "./DraftingView";
import * as api from "../api";
import * as images from "../images";

// canvas is dead in jsdom -> resizeImage returns a tiny blob (same stub as the sibling test).
beforeEach(() => {
  vi.spyOn(images, "resizeImage").mockImplementation(
    async (file: Blob) =>
      new Blob([new Uint8Array([1, 2, 3])], {
        type: (file as File).type || "image/jpeg",
      }),
  );
});

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

// Capture every backend payload; presign echoes one key per requested file.
function wireApi() {
  const calls: Record<string, unknown>[] = [];
  vi.spyOn(api, "postJson").mockImplementation(async (body) => {
    calls.push(body);
    switch (body.action) {
      case "issue-presigned-urls": {
        const files = body.files as { filename: string }[];
        return {
          uploads: files.map((_, i) => ({
            key: `images/img${i}.jpg`,
            url: `https://s3.example/put/${i}`,
            method: "PUT",
          })),
        } as never;
      }
      case "analyze-images":
        return { captions: [] } as never;
      case "generate-draft":
        return { draftId: "d1", markdown: "# t\n\n본문" } as never;
      default:
        return {} as never;
    }
  });
  return calls;
}

function makeFile(name: string): File {
  return new File([new Uint8Array([9, 9, 9])], name, { type: "image/jpeg" });
}

function attach(files: File[]) {
  const input = screen.getByLabelText("이미지 첨부") as HTMLInputElement;
  fireEvent.change(input, { target: { files } });
}

// Recursively assert no value is binary (File/Blob/ArrayBuffer/typed array) and the whole
// payload round-trips through JSON unchanged -- i.e. it carries data, never bytes.
function assertKeysOnly(payload: unknown, path = "$") {
  if (payload === null || typeof payload !== "object") return;
  expect(payload, `${path} must not be a Blob`).not.toBeInstanceOf(Blob);
  expect(payload, `${path} must not be a File`).not.toBeInstanceOf(File);
  expect(payload, `${path} must not be an ArrayBuffer`).not.toBeInstanceOf(ArrayBuffer);
  expect(ArrayBuffer.isView(payload), `${path} must not be a typed array`).toBe(false);
  for (const [k, v] of Object.entries(payload)) assertKeysOnly(v, `${path}.${k}`);
}

// (a) Cap invariant: hand the UI more than MAX_IMAGES; it never retains or ships more.
test("never retains or uploads more than MAX_IMAGES, however many are attached", async () => {
  const calls = wireApi();
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 200 }));

  render(<DraftingView />);
  fireEvent.change(screen.getByLabelText("토큰"), { target: { value: "t" } });
  fireEvent.change(screen.getByLabelText("제목"), { target: { value: "제목" } });

  const n = images.MAX_IMAGES + 7; // comfortably over the cap
  attach(Array.from({ length: n }, (_, i) => makeFile(`p${i}.jpg`)));

  // UI holds exactly the cap -- one description field per retained image.
  expect(screen.getAllByLabelText(/이미지 설명/)).toHaveLength(images.MAX_IMAGES);

  fireEvent.click(screen.getByRole("button", { name: "초안 생성" }));
  await screen.findByLabelText("초안 결과");

  // generate-draft never carries more keys than the cap allows.
  const gen = calls.find((c) => c.action === "generate-draft")!;
  const keys = gen.imageKeys as string[];
  expect(keys.length).toBeLessThanOrEqual(images.MAX_IMAGES);
  expect(keys.length).toBe(images.MAX_IMAGES);
});

// (b) Keys-only invariant: no backend payload holds a File/Blob/ArrayBuffer anywhere, and
// every payload is JSON-serializable (bytes go to S3 via fetch, never to the Lambda).
test("every backend payload is JSON-serializable and holds no binary (keys only)", async () => {
  const calls = wireApi();
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 200 }));

  render(<DraftingView />);
  fireEvent.change(screen.getByLabelText("토큰"), { target: { value: "t" } });
  fireEvent.change(screen.getByLabelText("제목"), { target: { value: "제목" } });
  attach([makeFile("a.jpg"), makeFile("b.jpg")]);
  fireEvent.change(screen.getByLabelText("이미지 설명 1"), {
    target: { value: "설명" },
  });

  fireEvent.click(screen.getByRole("button", { name: "초안 생성" }));
  await screen.findByLabelText("초안 결과");

  // Exercised the full 3-action path so analyze + presign payloads are covered too.
  expect(calls.map((c) => c.action)).toEqual([
    "issue-presigned-urls",
    "analyze-images",
    "generate-draft",
  ]);

  for (const payload of calls) {
    assertKeysOnly(payload); // deep: no binary at any depth
    // round-trips through JSON with no loss -> provably ships data, not bytes.
    expect(JSON.parse(JSON.stringify(payload))).toEqual(payload);
  }
});
