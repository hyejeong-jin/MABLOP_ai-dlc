import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ResultView } from "./ResultView";
import resultSource from "./ResultView.tsx?raw"; // raw source for the static localStorage check
import * as api from "../api";
import { ApiRequestError } from "../api";
import { formatKst } from "../format";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

const OLD = { draftId: "draft-old", title: "오래된 초안", createdAt: "2025-01-01T00:00:00.000Z" };
const NEW = { draftId: "draft-new", title: "제주 여행 후기", createdAt: "2025-01-02T03:04:05.678Z" };
const BODIES: Record<string, string> = {
  "draft-old": "# 오래된 초안\n\n옛날 본문",
  "draft-new": "# 제주 여행 후기\n\n본문 ![img-1]",
};

interface Wire {
  calls: Record<string, unknown>[];
  drafts: { draftId: string; title: string; createdAt: string }[];
}

// Wire postJson for the three actions this view uses; `drafts` is mutable so reloads see deletions.
function wireApi(initial = [OLD, NEW]): Wire {
  const wire: Wire = { calls: [], drafts: [...initial] };
  vi.spyOn(api, "postJson").mockImplementation(async (body) => {
    wire.calls.push(body);
    switch (body.action) {
      case "list-drafts":
        return { drafts: wire.drafts } as never;
      case "get-result":
        return { status: "ready", markdown: BODIES[body.id as string] ?? "" } as never;
      case "delete-draft":
        wire.drafts = wire.drafts.filter((d) => d.draftId !== body.draftId);
        return { draftId: body.draftId, deleted: true } as never;
      default:
        return {} as never;
    }
  });
  return wire;
}

function callsOf(wire: Wire, action: string) {
  return wire.calls.filter((c) => c.action === action);
}

function titlesInOrder(): string[] {
  return within(screen.getByRole("list", { name: "초안 기록" }))
    .getAllByRole("listitem")
    .map((li) => li.querySelector("button span")?.textContent ?? "");
}

// loading -> indicator while list-drafts is pending
test("shows a loading indicator while the list is pending", async () => {
  vi.spyOn(api, "postJson").mockImplementation(() => new Promise(() => {}));
  render(<ResultView />);
  expect(screen.getByRole("status")).toHaveTextContent("불러오는 중");
  expect(screen.queryByLabelText("생성된 마크다운")).not.toBeInTheDocument();
});

// Req 23.1, 23.2: list-drafts on open, newest first, title + KST time + delete button, no grantId
test("sends list-drafts on open and lists entries newest first", async () => {
  const wire = wireApi([OLD, NEW]);
  render(<ResultView />);

  await screen.findByText(NEW.title);
  expect(callsOf(wire, "list-drafts")).toHaveLength(1);
  expect(callsOf(wire, "list-drafts")[0]).not.toHaveProperty("grantId");
  expect(titlesInOrder()).toEqual([NEW.title, OLD.title]);

  const items = screen.getAllByRole("listitem");
  expect(items[0]).toHaveTextContent(formatKst(NEW.createdAt));
  expect(items[0]).toHaveTextContent(/12:04/); // 03:04Z is 12:04 in KST
  expect(screen.getAllByRole("button", { name: "삭제" })).toHaveLength(2);
  expect(screen.queryByText("아직 만든 초안이 없습니다.")).not.toBeInTheDocument();
});

// Req 23.3
test("shows the empty message when there are no drafts", async () => {
  wireApi([]);
  render(<ResultView />);
  expect(await screen.findByText("아직 만든 초안이 없습니다.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "삭제" })).not.toBeInTheDocument();
});

// Req 23.4: select -> get-result {kind: "draft", id}, body in <pre>, copy + download
test("selecting an entry fetches the body and shows copy and download", async () => {
  const wire = wireApi();
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.assign(navigator, { clipboard: { writeText } });

  render(<ResultView />);
  fireEvent.click(await screen.findByRole("button", { name: new RegExp(NEW.title) }));

  const output = await screen.findByLabelText("생성된 마크다운");
  expect(output).toHaveTextContent("# 제주 여행 후기");
  expect(output).toHaveTextContent("![img-1]");
  expect(output.tagName).toBe("PRE");
  expect(output).toHaveClass("draft-output");
  expect(callsOf(wire, "get-result")).toEqual([{ action: "get-result", kind: "draft", id: NEW.draftId }]);
  expect(screen.getByRole("listitem", { current: true })).toHaveTextContent(NEW.title);

  fireEvent.click(screen.getByRole("button", { name: "복사" }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(BODIES["draft-new"]));
  expect(await screen.findByText("복사됨")).toBeInTheDocument();
});

test("download saves the body as {draftId}.md with type text/markdown", async () => {
  wireApi();
  let blob: Blob | undefined;
  const createObjectURL = vi.fn((b: Blob) => {
    blob = b;
    return "blob:test";
  });
  const revokeObjectURL = vi.fn();
  Object.assign(URL, { createObjectURL, revokeObjectURL });
  let downloadName = "";
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    downloadName = this.download;
  });

  render(<ResultView />);
  fireEvent.click(await screen.findByRole("button", { name: new RegExp(NEW.title) }));
  await screen.findByLabelText("생성된 마크다운");
  fireEvent.click(screen.getByRole("button", { name: "다운로드" }));

  expect(downloadName).toBe("draft-new.md");
  expect(blob?.type).toBe("text/markdown");
  const text = await new Promise<string>((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.readAsText(blob as Blob);
  });
  expect(text).toBe(BODIES["draft-new"]);
  expect(revokeObjectURL).toHaveBeenCalledWith("blob:test");
});

// Req 23.5: focusDraftId is selected automatically, once
test("selects focusDraftId automatically and reports it handled once", async () => {
  const wire = wireApi();
  const onFocusHandled = vi.fn();
  const { rerender } = render(<ResultView focusDraftId={NEW.draftId} onFocusHandled={onFocusHandled} />);

  expect(await screen.findByLabelText("생성된 마크다운")).toHaveTextContent("# 제주 여행 후기");
  expect(callsOf(wire, "get-result")).toEqual([{ action: "get-result", kind: "draft", id: NEW.draftId }]);
  expect(onFocusHandled).toHaveBeenCalledTimes(1);

  // parent clears the hand-off: no second list load and no second handled call
  rerender(<ResultView focusDraftId={null} onFocusHandled={onFocusHandled} />);
  expect(callsOf(wire, "list-drafts")).toHaveLength(1);
  expect(onFocusHandled).toHaveBeenCalledTimes(1);
});

test("a focusDraftId that is not listed selects nothing but is still handled", async () => {
  const wire = wireApi();
  const onFocusHandled = vi.fn();
  render(<ResultView focusDraftId="draft-missing" onFocusHandled={onFocusHandled} />);

  await screen.findByText(NEW.title);
  expect(onFocusHandled).toHaveBeenCalledTimes(1);
  expect(callsOf(wire, "get-result")).toHaveLength(0);
  expect(screen.queryByLabelText("생성된 마크다운")).not.toBeInTheDocument();
});

// Req 23.6, 23.7: in-item confirmation, delete-draft, reload, clear the body of a deleted selection
test("delete asks for confirmation, then deletes, reloads, and clears the selected body", async () => {
  const wire = wireApi();
  render(<ResultView />);
  fireEvent.click(await screen.findByRole("button", { name: new RegExp(NEW.title) }));
  await screen.findByLabelText("생성된 마크다운");

  fireEvent.click(screen.getAllByRole("button", { name: "삭제" })[0]);
  expect(callsOf(wire, "delete-draft")).toHaveLength(0);
  expect(screen.getByRole("button", { name: "확인" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "취소" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "확인" }));
  await waitFor(() => expect(titlesInOrder()).toEqual([OLD.title]));
  expect(callsOf(wire, "delete-draft")).toEqual([{ action: "delete-draft", draftId: NEW.draftId }]);
  expect(callsOf(wire, "list-drafts")).toHaveLength(2);
  expect(screen.queryByLabelText("생성된 마크다운")).not.toBeInTheDocument();
  expect(screen.queryByRole("listitem", { current: true })).not.toBeInTheDocument();
});

test("deleting another entry keeps the selected body", async () => {
  wireApi();
  render(<ResultView />);
  fireEvent.click(await screen.findByRole("button", { name: new RegExp(NEW.title) }));
  await screen.findByLabelText("생성된 마크다운");

  fireEvent.click(screen.getAllByRole("button", { name: "삭제" })[1]); // OLD
  fireEvent.click(screen.getByRole("button", { name: "확인" }));
  await waitFor(() => expect(titlesInOrder()).toEqual([NEW.title]));
  expect(screen.getByLabelText("생성된 마크다운")).toHaveTextContent("# 제주 여행 후기");
});

test("cancel leaves the entry untouched and sends nothing", async () => {
  const wire = wireApi();
  render(<ResultView />);
  await screen.findByText(NEW.title);

  fireEvent.click(screen.getAllByRole("button", { name: "삭제" })[0]);
  fireEvent.click(screen.getByRole("button", { name: "취소" }));

  expect(callsOf(wire, "delete-draft")).toHaveLength(0);
  expect(screen.queryByRole("button", { name: "확인" })).not.toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "삭제" })).toHaveLength(2);
});

// Req 23.8: Korean backend message next to the affected area
test("list-drafts failure shows the backend message in an alert above the list", async () => {
  vi.spyOn(api, "postJson").mockRejectedValue(new ApiRequestError(500, "internal_error", "처리 중 오류가 발생했습니다."));
  render(<ResultView />);
  expect(await screen.findByRole("alert")).toHaveTextContent("처리 중 오류가 발생했습니다.");
  expect(screen.queryByText("아직 만든 초안이 없습니다.")).not.toBeInTheDocument();
});

test("get-result failure shows the backend message in the body pane", async () => {
  wireApi();
  const original = vi.mocked(api.postJson).getMockImplementation()!;
  vi.mocked(api.postJson).mockImplementation(async (body, token) => {
    if (body.action === "get-result") throw new ApiRequestError(404, "not_found", "대상 초안을 찾을 수 없습니다.");
    return original(body, token);
  });

  render(<ResultView />);
  fireEvent.click(await screen.findByRole("button", { name: new RegExp(NEW.title) }));
  expect(await screen.findByRole("alert")).toHaveTextContent("대상 초안을 찾을 수 없습니다.");
  expect(screen.queryByLabelText("생성된 마크다운")).not.toBeInTheDocument();
});

test("delete-draft failure shows the backend message under the affected entry", async () => {
  wireApi();
  const original = vi.mocked(api.postJson).getMockImplementation()!;
  vi.mocked(api.postJson).mockImplementation(async (body, token) => {
    if (body.action === "delete-draft") throw new ApiRequestError(500, "internal_error", "처리 중 오류가 발생했습니다.");
    return original(body, token);
  });

  render(<ResultView />);
  await screen.findByText(NEW.title);
  fireEvent.click(screen.getAllByRole("button", { name: "삭제" })[0]);
  fireEvent.click(screen.getByRole("button", { name: "확인" }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("처리 중 오류가 발생했습니다.");
  expect(screen.getAllByRole("listitem")[0]).toContainElement(alert);
  expect(titlesInOrder()).toEqual([NEW.title, OLD.title]);
});

// A body response that arrives after another entry was selected is discarded
test("discards a get-result response for an entry that is no longer selected", async () => {
  wireApi();
  const resolvers: Record<string, (v: { markdown: string }) => void> = {};
  const original = vi.mocked(api.postJson).getMockImplementation()!;
  vi.mocked(api.postJson).mockImplementation((body, token) => {
    if (body.action === "get-result") {
      return new Promise((resolve) => {
        resolvers[body.id as string] = resolve as never;
      });
    }
    return original(body, token);
  });

  render(<ResultView />);
  fireEvent.click(await screen.findByRole("button", { name: new RegExp(OLD.title) }));
  fireEvent.click(screen.getByRole("button", { name: new RegExp(NEW.title) }));

  resolvers["draft-new"]({ markdown: BODIES["draft-new"] });
  expect(await screen.findByLabelText("생성된 마크다운")).toHaveTextContent("제주 여행 후기");

  resolvers["draft-old"]({ markdown: BODIES["draft-old"] }); // late
  await Promise.resolve();
  await Promise.resolve();
  expect(screen.getByLabelText("생성된 마크다운")).toHaveTextContent("제주 여행 후기");
  expect(screen.getByLabelText("생성된 마크다운")).not.toHaveTextContent("옛날 본문");
});

// Req 23.9: single column at the existing 640px breakpoint
test("stylesheet stacks the history layout in one column at 640px and below", async () => {
  // Vitest returns an empty module for `index.css?raw`, so the file is read from disk.
  // @ts-ignore -- node:fs types are not installed in this project
  const { readFileSync } = await import("node:fs");
  const css: string = readFileSync("src/index.css", "utf8"); // vitest runs from frontend/
  expect(css).toMatch(/\.history-layout\s*\{[^}]*grid-template-columns:\s*minmax\(220px,\s*280px\)\s+1fr/);
  const media = css.slice(css.indexOf("@media (max-width: 640px)"));
  expect(media).toMatch(/\.history-layout\s*\{\s*grid-template-columns:\s*1fr;\s*\}/);
});

// Req 23.10: no localStorage use, no mablop-last-draft reference
test("does not read or write localStorage", async () => {
  const getItem = vi.spyOn(Storage.prototype, "getItem");
  const setItem = vi.spyOn(Storage.prototype, "setItem");
  wireApi();
  render(<ResultView focusDraftId={NEW.draftId} />);
  await screen.findByLabelText("생성된 마크다운");

  expect(getItem).not.toHaveBeenCalled();
  expect(setItem).not.toHaveBeenCalled();
  expect(resultSource).not.toMatch(/localStorage|mablop-last-draft/);
});

// Req 20.8, 20.9: no Grant selector, no grantId on any request
test("sends no grantId and renders no grant selector", async () => {
  const wire = wireApi();
  render(<ResultView focusDraftId={NEW.draftId} />);
  await screen.findByLabelText("생성된 마크다운");
  fireEvent.click(screen.getAllByRole("button", { name: "삭제" })[0]);
  fireEvent.click(screen.getByRole("button", { name: "확인" }));
  await waitFor(() => expect(callsOf(wire, "delete-draft")).toHaveLength(1));

  for (const call of wire.calls) expect(call).not.toHaveProperty("grantId");
  expect(screen.queryByLabelText(/AI 서비스 토큰 사용 권한/)).not.toBeInTheDocument();
});
