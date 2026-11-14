import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { VoiceBoxView } from "./VoiceBoxView";
import * as api from "../api";

type Body = Record<string, unknown>;

const summary = (over: Partial<api.VocSummary> = {}): api.VocSummary => ({
  vocId: "voc_1",
  preview: "온보딩이 어려워요",
  status: "new",
  createdAt: "2025-01-10T03:00:00.000Z",
  updatedAt: "2025-01-10T03:00:00.000Z",
  ...over,
});

const full: api.VocRecord = {
  vocId: "voc_1",
  body: "온보딩이 어려워요",
  userMetadata: { sourceType: "customer", tags: ["온보딩"], note: "메모" },
  status: "new",
  createdAt: "2025-01-10T03:00:00.000Z",
  updatedAt: "2025-01-10T03:00:00.000Z",
};

let calls: Body[];
let list: api.VocSummary[];

beforeEach(() => {
  calls = [];
  list = [summary(), summary({ vocId: "voc_2", preview: "모바일 결제 오류", status: "reviewing", createdAt: "2025-02-01T03:00:00.000Z" })];
  setHandler((body) => {
    switch (body.action) {
      case "list-voc": {
        const s = body.status as string | undefined;
        return { records: list.filter((r) => !s || r.status === s) };
      }
      default:
        return { voc: full };
    }
  });
});
afterEach(() => vi.unstubAllGlobals());

// The api helpers call postJson internally, so stub fetch (the real network boundary).
function setHandler(handler: (body: Body) => unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init: { body: string }) => {
      const body = JSON.parse(init.body) as Body;
      calls.push(body);
      try {
        const data = handler(body);
        return { ok: true, status: 200, json: async () => data };
      } catch (e) {
        const err = e as api.ApiRequestError;
        return { ok: false, status: err.status, json: async () => ({ error: { code: err.code, message: err.message } }) };
      }
    }),
  );
}

test("제목과 한글 상태 라벨 목록을 표시한다", async () => {
  render(<VoiceBoxView />);
  expect(screen.getByRole("heading", { name: "사용자의 목소리함" })).toBeTruthy();
  const listEl = await screen.findByRole("list", { name: "의견 목록" });
  expect(within(listEl).getByText("새 의견")).toBeTruthy();
  expect(within(listEl).getByText("검토 중")).toBeTruthy();
});

test("검색과 기간 필터는 목록을 좁히고 상태 필터는 서버에 전달한다", async () => {
  render(<VoiceBoxView />);
  await screen.findByRole("list", { name: "의견 목록" });
  fireEvent.change(screen.getByLabelText(/검색/), { target: { value: "결제" } });
  expect(screen.queryByText("온보딩이 어려워요")).toBeNull();
  expect(screen.getByText("모바일 결제 오류")).toBeTruthy();
  fireEvent.change(screen.getByLabelText(/검색/), { target: { value: "" } });
  fireEvent.change(screen.getByLabelText("시작일"), { target: { value: "2025-02-01" } });
  expect(screen.queryByText("온보딩이 어려워요")).toBeNull();
  fireEvent.change(screen.getByLabelText("시작일"), { target: { value: "" } });
  fireEvent.change(screen.getByLabelText("상태"), { target: { value: "reviewing" } });
  await waitFor(() => expect(calls.some((c) => c.action === "list-voc" && c.status === "reviewing")).toBe(true));
});

test("원문이 비면 필드 오류를 표시하고 전송하지 않는다", async () => {
  render(<VoiceBoxView />);
  await screen.findByRole("list", { name: "의견 목록" });
  fireEvent.click(screen.getByRole("button", { name: "의견 저장" }));
  const alert = await screen.findByText("원문을 입력하세요.");
  expect(alert.getAttribute("role")).toBe("alert");
  expect(screen.getByLabelText("원문").getAttribute("aria-describedby")).toContain("voc-body-error");
  expect(calls.some((c) => c.action === "create-voc")).toBe(false);
});

test("원문·출처 유형·태그·메모를 분리해 create-voc로 보낸다", async () => {
  render(<VoiceBoxView />);
  await screen.findByRole("list", { name: "의견 목록" });
  fireEvent.change(screen.getByLabelText("원문"), { target: { value: "가격이 비싸요" } });
  fireEvent.change(screen.getByLabelText("출처 유형"), { target: { value: "quote" } });
  fireEvent.change(screen.getByLabelText(/태그/), { target: { value: "가격, 결제 ," } });
  fireEvent.change(screen.getByLabelText("메모"), { target: { value: "인터뷰" } });
  fireEvent.click(screen.getByRole("button", { name: "의견 저장" }));
  await waitFor(() =>
    expect(calls).toContainEqual({
      action: "create-voc",
      body: "가격이 비싸요",
      sourceType: "quote",
      tags: ["가격", "결제"],
      note: "인터뷰",
    }),
  );
  expect(await screen.findByText("의견을 저장했습니다.")).toBeTruthy();
});

test("서버 오류는 필드 오류와 별도 alert로 표시한다", async () => {
  render(<VoiceBoxView />);
  await screen.findByRole("list", { name: "의견 목록" });
  setHandler((body) => {
    if (body.action === "create-voc") throw new api.ApiRequestError(400, "bad_request", "sourceType is invalid");
    return { records: [] };
  });
  fireEvent.change(screen.getByLabelText("원문"), { target: { value: "내용" } });
  fireEvent.click(screen.getByRole("button", { name: "의견 저장" }));
  expect(await screen.findByText("sourceType is invalid")).toBeTruthy();
});

test("수정은 get-voc 후 update-voc, 보관은 archive-voc를 호출한다", async () => {
  render(<VoiceBoxView />);
  const listEl = await screen.findByRole("list", { name: "의견 목록" });
  fireEvent.click(within(listEl).getAllByRole("button", { name: "수정" })[0]);
  await waitFor(() => expect(calls).toContainEqual({ action: "get-voc", vocId: "voc_1" }));
  await waitFor(() => expect((screen.getByLabelText("원문") as HTMLTextAreaElement).value).toBe("온보딩이 어려워요"));
  fireEvent.click(screen.getByRole("button", { name: "수정 저장" }));
  await waitFor(() => expect(calls.some((c) => c.action === "update-voc" && c.vocId === "voc_1")).toBe(true));

  fireEvent.click(within(await screen.findByRole("list", { name: "의견 목록" })).getAllByRole("button", { name: "보관" })[0]);
  await waitFor(() => expect(calls).toContainEqual({ action: "archive-voc", vocId: "voc_1" }));
});
