import { render, screen, fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { DraftingView } from "./DraftingView";
import draftingSource from "./DraftingView.tsx?raw"; // raw source for the static SDK check
import * as api from "../api";
import * as images from "../images";

// resizeImage uses canvas (not functional in jsdom) -> stub to a tiny blob.
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

// Wire postJson to walk the 3 backend actions in order; capture their payloads.
function wireApi() {
  const calls: Record<string, unknown>[] = [];
  const postJson = vi.spyOn(api, "postJson").mockImplementation(async (body) => {
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
        return {
          draftId: "draft-1",
          markdown: "# 제목\n\n본문 ![img-1]",
        } as never;
      default:
        return {} as never;
    }
  });
  return { calls, postJson };
}

function makeFile(name: string): File {
  return new File([new Uint8Array([9, 9, 9])], name, { type: "image/jpeg" });
}

function attach(files: File[]) {
  const input = screen.getByLabelText("이미지 첨부") as HTMLInputElement;
  fireEvent.change(input, { target: { files } });
}

// 10-image cap enforced in the UI (Req 7.4).
test("caps attached images at MAX_IMAGES", () => {
  render(<DraftingView />);
  const twelve = Array.from({ length: 12 }, (_, i) => makeFile(`p${i}.jpg`));
  attach(twelve);
  // one description field rendered per retained image
  expect(screen.getAllByLabelText(/이미지 설명/)).toHaveLength(images.MAX_IMAGES);
});

// Full upload flow: presign -> S3 PUT (raw fetch) -> analyze -> generate; keys only.
test("uploads via presigned PUT and sends only keys to the backend", async () => {
  const { calls } = wireApi();
  // global fetch stands in for the direct-to-S3 PUT.
  const fetchMock = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(new Response(null, { status: 200 }));

  render(<DraftingView />);
  fireEvent.change(screen.getByLabelText("토큰"), { target: { value: "secret" } });
  fireEvent.change(screen.getByLabelText("제목"), { target: { value: "제목" } });
  attach([makeFile("a.jpg"), makeFile("b.jpg")]);
  fireEvent.change(screen.getByLabelText("이미지 설명 1"), {
    target: { value: "사용자 설명" },
  });

  fireEvent.click(screen.getByRole("button", { name: "초안 생성" }));

  // generate-draft eventually produces rendered markdown.
  expect(await screen.findByLabelText("초안 결과")).toHaveTextContent("본문");

  // S3 PUT went via raw fetch (not api.ts), with blob bodies.
  expect(fetchMock).toHaveBeenCalledTimes(2);
  for (const call of fetchMock.mock.calls) {
    const init = call[1] as RequestInit;
    expect(init.method).toBe("PUT");
    expect(init.body).toBeInstanceOf(Blob);
  }

  // backend saw all three actions, in order.
  const actions = calls.map((c) => c.action);
  expect(actions).toEqual([
    "issue-presigned-urls",
    "analyze-images",
    "generate-draft",
  ]);

  // analyze-images carries keys (+ optional desc) only -- no File/Blob/binary.
  const analyze = calls.find((c) => c.action === "analyze-images")!;
  const analyzeImages = analyze.images as Record<string, unknown>[];
  expect(analyzeImages).toEqual([
    { key: "images/img0.jpg" },
    { key: "images/img1.jpg", description: "사용자 설명" },
  ]);

  // generate-draft carries imageKeys only; nothing in the payload is a File/Blob.
  const gen = calls.find((c) => c.action === "generate-draft")!;
  expect(gen.imageKeys).toEqual(["images/img0.jpg", "images/img1.jpg"]);
  for (const c of calls) {
    for (const v of Object.values(c)) {
      expect(v).not.toBeInstanceOf(Blob);
      expect(v).not.toBeInstanceOf(File);
    }
  }
});

// generate-draft works with no images; renders returned markdown (synchronous UX).
test("generates a draft with no images and renders markdown", async () => {
  const { calls } = wireApi();
  render(<DraftingView />);
  fireEvent.change(screen.getByLabelText("토큰"), { target: { value: "t" } });
  fireEvent.change(screen.getByLabelText("제목"), { target: { value: "x" } });
  fireEvent.click(screen.getByRole("button", { name: "초안 생성" }));

  expect(await screen.findByLabelText("초안 결과")).toHaveTextContent("본문");
  const actions = calls.map((c) => c.action);
  expect(actions).toEqual(["generate-draft"]);
  expect((calls[0].imageKeys as string[])).toEqual([]);
});

// Design Principle 1 / Req 6.1: the view must never import a Bedrock/AWS SDK.
test("imports no Bedrock/AWS SDK", () => {
  // only inspect import lines (comments may mention Bedrock/AWS SDK by name).
  const importLines = draftingSource
    .split("\n")
    .filter((line: string) => /^\s*import\b/.test(line))
    .join("\n");
  expect(importLines).not.toMatch(/@aws-sdk|aws-sdk|bedrock/i);
});
