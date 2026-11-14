import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { VoiceBoxView } from "./VoiceBoxView";
import * as api from "../api";

type Body = Record<string, unknown>;

// The view reads the session user through useAuth(); the tests choose the role.
const auth = vi.hoisted(() => ({
  user: null as { loginId: string; name: string; role: "member" | "admin" } | null,
}));
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    user: auth.user,
    notice: null,
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    replaceToken: vi.fn(),
  }),
}));

const summary = (over: Partial<api.VocSummary> = {}): api.VocSummary => ({
  vocId: "voc_1",
  title: "온보딩이 어려워요",
  category: "inquiry",
  hasReply: false,
  preview: "처음 쓰는데 어디서 시작하나요",
  status: "new",
  createdAt: "2025-01-10T03:00:00.000Z",
  updatedAt: "2025-01-10T03:00:00.000Z",
  ...over,
});

const full = (over: Partial<api.VocRecord> = {}): api.VocRecord => ({
  vocId: "voc_1",
  category: "inquiry",
  title: "온보딩이 어려워요",
  body: "처음 쓰는데 어디서 시작하나요",
  status: "new",
  createdAt: "2025-01-10T03:00:00.000Z",
  updatedAt: "2025-01-10T03:00:00.000Z",
  reply: null,
  ...over,
});

const reply: api.VocReply = {
  body: "스타일 학습부터 시작하세요.",
  createdAt: "2025-01-11T03:00:00.000Z",
  updatedAt: "2025-01-11T03:00:00.000Z",
  authorLoginId: "mablop_admin",
};

let calls: Body[];
let list: api.VocSummary[];
let detail: api.VocRecord;

beforeEach(() => {
  calls = [];
  auth.user = { loginId: "u1", name: "회원", role: "member" };
  list = [
    summary({ hasReply: true }),
    summary({ vocId: "voc_2", title: "결제 오류", category: "bug", status: "reviewing", createdAt: "2025-02-01T03:00:00.000Z" }),
  ];
  detail = full({ reply });
  setHandler((body) => {
    switch (body.action) {
      case "list-voc":
      case "admin-list-voc": {
        const s = body.status as string | undefined;
        return { records: list.filter((r) => !s || r.status === s) };
      }
      case "get-voc":
        return { voc: detail };
      default:
        return { voc: detail };
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

test("회원: 안내 문구, 번호·분류·작성일·상태 표와 Re 행을 표시한다", async () => {
  render(<VoiceBoxView />);
  expect(screen.getByRole("heading", { name: "사용자의 목소리함" })).toBeTruthy();
  expect(screen.getByText(/남긴 글은 운영자와 나만 볼 수 있어요/)).toBeTruthy();
  const table = await screen.findByRole("table", { name: "문의 목록" });
  const rows = within(table).getAllByRole("row");
  // header + (voc_2 row) + (voc_1 row + Re row)
  expect(rows).toHaveLength(4);
  expect(within(rows[1]).getByText("2")).toBeTruthy();
  expect(within(rows[1]).getByText("오류")).toBeTruthy();
  expect(within(rows[1]).getByText("2025-02-01")).toBeTruthy();
  expect(within(rows[1]).getByText("검토 중")).toBeTruthy();
  expect(within(table).getAllByText("Re: 운영자 답변")).toHaveLength(1);
  expect(within(table).queryByText("작성자")).toBeNull();
});

test("회원: 제목을 누르면 get-voc로 본문과 답변을 펼치고 다시 누르면 접는다", async () => {
  render(<VoiceBoxView />);
  const btn = await screen.findByRole("button", { name: "온보딩이 어려워요" });
  expect(btn.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(btn);
  expect(btn.getAttribute("aria-expanded")).toBe("true");
  expect(await screen.findByText("스타일 학습부터 시작하세요.")).toBeTruthy();
  expect(screen.getByText("처음 쓰는데 어디서 시작하나요")).toBeTruthy();
  expect(calls).toContainEqual({ action: "get-voc", vocId: "voc_1" });
  fireEvent.click(btn);
  expect(btn.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByText("스타일 학습부터 시작하세요.")).toBeNull();
});

test("회원: 분류 필터, 제목 검색(검색 버튼), 상태 필터(서버 전달)", async () => {
  render(<VoiceBoxView />);
  await screen.findByRole("table", { name: "문의 목록" });
  fireEvent.change(screen.getByLabelText("분류 필터"), { target: { value: "bug" } });
  expect(screen.queryByText("온보딩이 어려워요")).toBeNull();
  expect(screen.getByText("결제 오류")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("분류 필터"), { target: { value: "" } });
  fireEvent.change(screen.getByLabelText("제목 검색"), { target: { value: "온보딩" } });
  expect(screen.getByText("결제 오류")).toBeTruthy(); // 검색 버튼 전에는 적용 안 됨
  fireEvent.click(screen.getByRole("button", { name: "검색" }));
  expect(screen.queryByText("결제 오류")).toBeNull();
  fireEvent.change(screen.getByLabelText("상태 필터"), { target: { value: "reviewing" } });
  await waitFor(() => expect(calls.some((c) => c.action === "list-voc" && c.status === "reviewing")).toBe(true));
});

test("회원: 문의가 없으면 안내 문구를 보여준다", async () => {
  list = [];
  render(<VoiceBoxView />);
  expect(await screen.findByText("아직 남긴 문의가 없어요.")).toBeTruthy();
});

test("회원: 제목·내용이 비면 필드 오류를 표시하고 전송하지 않는다", async () => {
  render(<VoiceBoxView />);
  await screen.findByRole("table", { name: "문의 목록" });
  fireEvent.click(screen.getByRole("button", { name: "문의 남기기" }));
  fireEvent.click(screen.getByRole("button", { name: "등록" }));
  expect((await screen.findByText("제목을 입력하세요.")).getAttribute("role")).toBe("alert");
  expect(screen.getByText("내용을 입력하세요.")).toBeTruthy();
  expect(screen.getByLabelText("제목").getAttribute("aria-describedby")).toContain("error");
  fireEvent.change(screen.getByLabelText("제목"), { target: { value: "x".repeat(101) } });
  fireEvent.click(screen.getByRole("button", { name: "등록" }));
  expect(await screen.findByText("제목은 100자 이하로 입력하세요.")).toBeTruthy();
  expect(calls.some((c) => c.action === "create-voc")).toBe(false);
  // 태그·메모·출처 유형 필드는 없다
  expect(screen.queryByLabelText("태그")).toBeNull();
  expect(screen.queryByLabelText("메모")).toBeNull();
});

test("회원: 분류·제목·내용을 create-voc로 보낸다", async () => {
  render(<VoiceBoxView />);
  await screen.findByRole("table", { name: "문의 목록" });
  fireEvent.click(screen.getByRole("button", { name: "문의 남기기" }));
  fireEvent.change(screen.getByLabelText("분류"), { target: { value: "suggestion" } });
  fireEvent.change(screen.getByLabelText("제목"), { target: { value: "다크 모드" } });
  fireEvent.change(screen.getByLabelText("내용"), { target: { value: "야간에 눈이 부셔요" } });
  fireEvent.click(screen.getByRole("button", { name: "등록" }));
  await waitFor(() =>
    expect(calls).toContainEqual({ action: "create-voc", category: "suggestion", title: "다크 모드", body: "야간에 눈이 부셔요" }),
  );
  expect(await screen.findByText("문의를 남겼어요.")).toBeTruthy();
});

test("회원: 서버 오류는 별도 alert로 표시한다", async () => {
  render(<VoiceBoxView />);
  await screen.findByRole("table", { name: "문의 목록" });
  setHandler((body) => {
    if (body.action === "create-voc") throw new api.ApiRequestError(400, "bad_request", "title is invalid");
    return { records: [] };
  });
  fireEvent.click(screen.getByRole("button", { name: "문의 남기기" }));
  fireEvent.change(screen.getByLabelText("제목"), { target: { value: "제목" } });
  fireEvent.change(screen.getByLabelText("내용"), { target: { value: "내용" } });
  fireEvent.click(screen.getByRole("button", { name: "등록" }));
  expect(await screen.findByText("title is invalid")).toBeTruthy();
});

test("회원: 펼친 본인 글을 수정(update-voc)하고 삭제(archive-voc)한다", async () => {
  render(<VoiceBoxView />);
  fireEvent.click(await screen.findByRole("button", { name: "온보딩이 어려워요" }));
  fireEvent.click(await screen.findByRole("button", { name: "수정" }));
  fireEvent.change(screen.getByLabelText("제목"), { target: { value: "새 제목" } });
  fireEvent.click(screen.getByRole("button", { name: "수정 저장" }));
  await waitFor(() =>
    expect(calls).toContainEqual({
      action: "update-voc",
      vocId: "voc_1",
      category: "inquiry",
      title: "새 제목",
      body: "처음 쓰는데 어디서 시작하나요",
    }),
  );
  fireEvent.click(await screen.findByRole("button", { name: "삭제" }));
  fireEvent.click(screen.getByRole("button", { name: "삭제 확인" }));
  await waitFor(() => expect(calls).toContainEqual({ action: "archive-voc", vocId: "voc_1" }));
});

test("관리자: 작성 버튼·폼이 없고 admin-list-voc로 작성자 열과 전체 문의를 보여준다", async () => {
  auth.user = { loginId: "mablop_admin", name: "관리자", role: "admin" };
  list = [summary({ ownerId: "u_1", ownerLoginId: "member1" })];
  render(<VoiceBoxView />);
  expect(screen.getByText("회원들이 남긴 문의에 답변할 수 있어요.")).toBeTruthy();
  const table = await screen.findByRole("table", { name: "문의 목록" });
  expect(within(table).getByText("작성자")).toBeTruthy();
  expect(within(table).getByText("member1")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "문의 남기기" })).toBeNull();
  expect(calls.some((c) => c.action === "admin-list-voc")).toBe(true);
  expect(calls.some((c) => c.action === "list-voc")).toBe(false);
});

test("관리자: 답변 등록(admin-reply)과 상태 변경(admin-update-voc-status)", async () => {
  auth.user = { loginId: "mablop_admin", name: "관리자", role: "admin" };
  list = [summary({ ownerId: "u_1", ownerLoginId: "member1" })];
  detail = full({ reply: null });
  setHandler((body) => {
    if (body.action === "admin-list-voc") return { records: list };
    if (body.action === "admin-reply") return { voc: full({ reply }) };
    return { voc: detail };
  });
  render(<VoiceBoxView />);
  fireEvent.click(await screen.findByRole("button", { name: "온보딩이 어려워요" }));

  fireEvent.click(await screen.findByRole("button", { name: "답변 등록" }));
  expect(await screen.findByText("답변을 입력하세요.")).toBeTruthy();
  expect(calls.some((c) => c.action === "admin-reply")).toBe(false);

  fireEvent.change(screen.getByLabelText("답변"), { target: { value: "확인했어요" } });
  fireEvent.click(screen.getByRole("button", { name: "답변 등록" }));
  await waitFor(() =>
    expect(calls).toContainEqual({ action: "admin-reply", ownerId: "u_1", vocId: "voc_1", body: "확인했어요" }),
  );
  expect(await screen.findByText("답변을 등록했어요.")).toBeTruthy();
  expect(await screen.findByRole("button", { name: "답변 수정" })).toBeTruthy();

  fireEvent.change(screen.getByLabelText("상태 변경"), { target: { value: "confirmed" } });
  await waitFor(() =>
    expect(calls).toContainEqual({ action: "admin-update-voc-status", userId: "u_1", vocId: "voc_1", status: "confirmed" }),
  );
});

test("관리자: 펼치면 admin-get-voc로 전체 본문과 기존 답변을 불러오고 입력창에 채운다", async () => {
  auth.user = { loginId: "mablop_admin", name: "관리자", role: "admin" };
  list = [summary({ hasReply: true, ownerId: "u_1", ownerLoginId: "member1", preview: "처음 쓰는데..." })];
  detail = full({ body: "처음 쓰는데 어디서 시작하나요. 전체 본문입니다.", reply });
  render(<VoiceBoxView />);
  fireEvent.click(await screen.findByRole("button", { name: "온보딩이 어려워요" }));

  expect(await screen.findByText("처음 쓰는데 어디서 시작하나요. 전체 본문입니다.")).toBeTruthy();
  expect(calls).toContainEqual({ action: "admin-get-voc", ownerId: "u_1", vocId: "voc_1" });
  expect(screen.getAllByText("스타일 학습부터 시작하세요.").length).toBeGreaterThan(0);
  expect((screen.getByLabelText("답변") as HTMLTextAreaElement).value).toBe("스타일 학습부터 시작하세요.");
  expect(screen.getByRole("button", { name: "답변 수정" })).toBeTruthy();
});
