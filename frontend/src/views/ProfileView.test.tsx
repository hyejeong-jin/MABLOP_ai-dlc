import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ProfileView } from "./ProfileView";
import * as api from "../api";
import { ApiRequestError } from "../api";
import { formatKst } from "../format";

// ProfileView reads the user, logout and replaceToken through useAuth(); the tests control them.
const auth = vi.hoisted(() => ({
  user: null as { loginId: string; name: string; role: string } | null,
  logout: vi.fn(),
  replaceToken: vi.fn(),
}));
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    user: auth.user,
    notice: null,
    login: async () => {},
    register: async () => "",
    logout: auth.logout,
    replaceToken: auth.replaceToken,
  }),
}));

type Body = Record<string, unknown>;
type Handler = (body: Body) => unknown;

interface Grant {
  id: string;
  status: string;
  purpose: string | null;
  requestedAt: string;
  decidedAt: string | null;
  expiresAt: string | null;
  expired: boolean;
}

const ME = { loginId: "alice01", name: "앨리스", role: "member" };
const LEARNING = { processedPosts: 3, chunks: 12, updatedAt: "2025-01-02T03:04:05.678Z" };

function grant(over: Partial<Grant> = {}): Grant {
  return {
    id: "g_0000000000000001",
    status: "approved",
    purpose: null,
    requestedAt: "2025-01-02T03:04:05.678Z",
    decidedAt: null,
    expiresAt: null,
    expired: false,
    ...over,
  };
}

// Routes postJson by action and records every body. Unlisted actions resolve with sensible defaults.
function wireApi(handlers: Record<string, Handler> = {}) {
  const calls: Body[] = [];
  vi.spyOn(api, "postJson").mockImplementation((async (body: Body) => {
    calls.push(body);
    const handler = handlers[body.action as string];
    if (handler) return handler(body);
    switch (body.action) {
      case "get-me":
        return ME;
      case "get-learning-status":
        return LEARNING;
      case "grant-list-mine":
        return { grants: [] };
      default:
        return {};
    }
  }) as never);
  return calls;
}

function actions(calls: Body[], action: string): Body[] {
  return calls.filter((c) => c.action === action);
}

beforeEach(() => {
  auth.user = { loginId: "alice01", name: "앨리스", role: "member" };
  auth.logout.mockReset();
  auth.replaceToken.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

function field(label: string): HTMLInputElement {
  return screen.getByLabelText(label) as HTMLInputElement;
}

function type(label: string, value: string) {
  fireEvent.change(field(label), { target: { value } });
}

function passwordForm(): HTMLFormElement {
  return screen.getByRole("button", { name: "비밀번호 변경" }).closest("form") as HTMLFormElement;
}

function grantForm(): HTMLFormElement {
  return screen.getByRole("button", { name: "사용 권한 신청" }).closest("form") as HTMLFormElement;
}

function fillPasswords(current: string, next: string, confirm: string) {
  type("현재 비밀번호", current);
  type("새 비밀번호", next);
  type("새 비밀번호 확인", confirm);
}

test("calls get-me and get-learning-status in parallel on mount", async () => {
  const pending: Record<string, () => void> = {};
  const calls: string[] = [];
  vi.spyOn(api, "postJson").mockImplementation(((body: Body) => {
    calls.push(body.action as string);
    return new Promise((resolve) => {
      pending[body.action as string] = () => resolve(body.action === "get-me" ? ME : LEARNING);
    });
  }) as never);

  render(<ProfileView />);

  // Both requests are in flight before either one has resolved.
  expect(calls).toContain("get-me");
  expect(calls).toContain("get-learning-status");
  pending["get-me"]?.();
  pending["get-learning-status"]?.();
  expect(await screen.findByText("앨리스")).toBeInTheDocument();
});

test("shows name and login ID read-only", async () => {
  wireApi();
  render(<ProfileView />);

  const account = await screen.findByRole("region", { name: "계정" });
  const items = within(account);
  expect(await items.findByText("앨리스")).toBeInTheDocument();
  expect(items.getByText("이름")).toBeInTheDocument();
  expect(items.getByText("아이디")).toBeInTheDocument();
  expect(items.getByText("alice01")).toBeInTheDocument();
  expect(within(account).queryAllByRole("textbox")).toHaveLength(0);
  expect(screen.queryByDisplayValue("앨리스")).toBeNull();
  expect(screen.queryByDisplayValue("alice01")).toBeNull();
});

test("learning summary shows posts, chunks and the KST last-learned time", async () => {
  wireApi();
  render(<ProfileView />);

  const section = await screen.findByRole("region", { name: "학습 요약" });
  expect(await within(section).findByText("학습한 글 3개")).toBeInTheDocument();
  expect(within(section).getByText("청크 12개")).toBeInTheDocument();
  const expected = `최근 학습 ${formatKst(LEARNING.updatedAt)}`;
  expect(within(section).getByText(expected)).toBeInTheDocument();
  // 03:04 UTC is 12:04 in KST.
  expect(expected).toContain("12:04");
});

test("zero learned posts shows the empty message instead of the counts", async () => {
  wireApi({ "get-learning-status": () => ({ processedPosts: 0, chunks: 0 }) });
  render(<ProfileView />);

  const section = await screen.findByRole("region", { name: "학습 요약" });
  expect(await within(section).findByText("아직 학습한 글이 없습니다.")).toBeInTheDocument();
  expect(within(section).queryByText(/학습한 글 \d+개/)).toBeNull();
});

test("a get-me failure does not hide the learning summary", async () => {
  wireApi({
    "get-me": () => {
      throw new ApiRequestError(500, "internal_error", "내 정보 오류");
    },
  });
  render(<ProfileView />);

  const account = await screen.findByRole("region", { name: "계정" });
  expect(await within(account).findByRole("alert")).toHaveTextContent("내 정보 오류");
  const learning = screen.getByRole("region", { name: "학습 요약" });
  expect(await within(learning).findByText("학습한 글 3개")).toBeInTheDocument();
  expect(within(learning).queryByRole("alert")).toBeNull();
  expect(screen.getByRole("button", { name: "비밀번호 변경" })).toBeInTheDocument();
});

test("a get-learning-status failure does not hide the account", async () => {
  wireApi({
    "get-learning-status": () => {
      throw new ApiRequestError(500, "internal_error", "학습 현황 오류");
    },
  });
  render(<ProfileView />);

  const learning = await screen.findByRole("region", { name: "학습 요약" });
  expect(await within(learning).findByRole("alert")).toHaveTextContent("학습 현황 오류");
  const account = screen.getByRole("region", { name: "계정" });
  expect(await within(account).findByText("앨리스")).toBeInTheDocument();
  expect(within(account).queryByRole("alert")).toBeNull();
});

test("falls back to Korean messages when the loads fail without a backend message", async () => {
  vi.spyOn(api, "postJson").mockRejectedValue(new TypeError("Failed to fetch"));
  auth.user = { loginId: "root", name: "관리자", role: "admin" };
  render(<ProfileView />);

  expect(await screen.findByText("내 정보를 불러오지 못했습니다.")).toBeInTheDocument();
  expect(await screen.findByText("학습 현황을 불러오지 못했습니다.")).toBeInTheDocument();
  expect(screen.queryByText(/Failed to fetch/)).toBeNull();
});

test("mismatched new passwords show the Korean message and send no change request", async () => {
  const calls = wireApi();
  render(<ProfileView />);
  await screen.findByText("앨리스");

  fillPasswords("old-password-1", "new-password-1", "new-password-2");
  fireEvent.submit(passwordForm());

  expect(await screen.findByText("비밀번호가 일치하지 않습니다.")).toBeInTheDocument();
  expect(actions(calls, "change-password")).toHaveLength(0);
  expect(auth.replaceToken).not.toHaveBeenCalled();
});

test("successful password change replaces the token, clears the form and confirms", async () => {
  const calls = wireApi({
    "change-password": () => ({ token: "new.token", expiresAt: "2025-01-02T11:04:05.678Z" }),
  });
  render(<ProfileView />);
  await screen.findByText("앨리스");

  fillPasswords("old-password-1", "new-password-1", "new-password-1");
  fireEvent.submit(passwordForm());

  expect(await screen.findByText("비밀번호가 변경되었습니다.")).toBeInTheDocument();
  expect(actions(calls, "change-password")).toEqual([
    { action: "change-password", currentPassword: "old-password-1", newPassword: "new-password-1" },
  ]);
  expect(auth.replaceToken).toHaveBeenCalledWith("new.token", "2025-01-02T11:04:05.678Z");
  expect(field("현재 비밀번호").value).toBe("");
  expect(field("새 비밀번호").value).toBe("");
  expect(field("새 비밀번호 확인").value).toBe("");
  expect(screen.queryByText("비밀번호가 일치하지 않습니다.")).toBeNull();
});

test("password inputs are masked", async () => {
  wireApi();
  render(<ProfileView />);
  await screen.findByText("앨리스");

  for (const label of ["현재 비밀번호", "새 비밀번호", "새 비밀번호 확인"]) {
    expect(field(label)).toHaveAttribute("type", "password");
  }
});

test("a failed password change shows the backend message and keeps the token", async () => {
  wireApi({
    "change-password": () => {
      throw new ApiRequestError(400, "bad_request", "현재 비밀번호가 올바르지 않습니다.");
    },
  });
  render(<ProfileView />);
  await screen.findByText("앨리스");

  fillPasswords("wrong-password-1", "new-password-1", "new-password-1");
  fireEvent.submit(passwordForm());

  const alert = await screen.findByText("현재 비밀번호가 올바르지 않습니다.");
  expect(alert.closest('[role="alert"]')).not.toBeNull();
  expect(auth.replaceToken).not.toHaveBeenCalled();
  expect(screen.queryByText("비밀번호가 변경되었습니다.")).toBeNull();
});

test("a password change failure without a backend message shows the Korean fallback", async () => {
  wireApi({
    "change-password": () => {
      throw new TypeError("Failed to fetch");
    },
  });
  render(<ProfileView />);
  await screen.findByText("앨리스");

  fillPasswords("old-password-1", "new-password-1", "new-password-1");
  fireEvent.submit(passwordForm());

  expect(
    await screen.findByText("비밀번호를 변경하지 못했습니다. 잠시 후 다시 시도하세요."),
  ).toBeInTheDocument();
  expect(screen.queryByText(/Failed to fetch/)).toBeNull();
});

test("the 로그아웃 button calls logout", async () => {
  wireApi();
  render(<ProfileView />);
  await screen.findByText("앨리스");

  fireEvent.click(screen.getByRole("button", { name: "로그아웃" }));
  expect(auth.logout).toHaveBeenCalledTimes(1);
});

test("has no draft list, account deletion or usage statistics", async () => {
  wireApi();
  render(<ProfileView />);
  await screen.findByText("앨리스");

  const text = document.body.textContent ?? "";
  expect(text).not.toMatch(/초안/);
  expect(text).not.toMatch(/계정 삭제|탈퇴/);
  expect(text).not.toMatch(/사용량|통계/);
  expect(screen.queryByRole("button", { name: /삭제/ })).toBeNull();
});

test("members see the Bedrock 사용 권한 section; admins do not", async () => {
  const memberCalls = wireApi();
  const first = render(<ProfileView />);
  expect(await screen.findByRole("region", { name: "Bedrock 사용 권한" })).toBeInTheDocument();
  await waitFor(() => expect(actions(memberCalls, "grant-list-mine")).toHaveLength(1));
  first.unmount();
  vi.restoreAllMocks();

  auth.user = { loginId: "root", name: "관리자", role: "admin" };
  const adminCalls = wireApi();
  render(<ProfileView />);
  await screen.findByText("앨리스");

  expect(screen.queryByRole("region", { name: "Bedrock 사용 권한" })).toBeNull();
  expect(screen.queryByText("Bedrock 사용 권한")).toBeNull();
  expect(screen.queryByRole("button", { name: "사용 권한 신청" })).toBeNull();
  expect(actions(adminCalls, "grant-list-mine")).toHaveLength(0);
});

test("the purpose input is optional and limited to 100 characters", async () => {
  wireApi();
  render(<ProfileView />);
  await screen.findByText("아직 신청한 사용 권한이 없습니다.");

  const purpose = field("사용 목적");
  expect(purpose).toHaveAttribute("maxlength", "100");
  expect(purpose).not.toBeRequired();
});

test("requesting a grant sends the trimmed purpose, clears the form and reloads the list", async () => {
  let listed: Grant[] = [];
  const calls = wireApi({
    "grant-list-mine": () => ({ grants: listed }),
    "grant-request": () => {
      listed = [grant({ id: "g_0000000000000002", status: "requested", purpose: "블로그 초안" })];
      return { id: "g_0000000000000002" };
    },
  });
  render(<ProfileView />);
  await screen.findByText("아직 신청한 사용 권한이 없습니다.");

  type("사용 목적", "  블로그 초안  ");
  fireEvent.submit(grantForm());

  expect(await screen.findByText("승인 대기 중인 신청이 있습니다.")).toBeInTheDocument();
  expect(actions(calls, "grant-request")).toEqual([{ action: "grant-request", purpose: "블로그 초안" }]);
  expect(actions(calls, "grant-list-mine")).toHaveLength(2);
  expect(field("사용 목적").value).toBe("");
  const list = screen.getByRole("list", { name: "사용 권한 신청 내역" });
  expect(within(list).getByText("대기")).toBeInTheDocument();
  expect(within(list).getByText("블로그 초안")).toBeInTheDocument();
});

test("a whitespace-only purpose is sent as null", async () => {
  const calls = wireApi();
  render(<ProfileView />);
  await screen.findByText("아직 신청한 사용 권한이 없습니다.");

  type("사용 목적", "     ");
  fireEvent.submit(grantForm());

  await waitFor(() => expect(actions(calls, "grant-request")).toHaveLength(1));
  expect(actions(calls, "grant-request")[0]).toEqual({ action: "grant-request", purpose: null });
});

test("shows the five status badges, request time in KST and the approval expiry date", async () => {
  const requestedAt = "2025-01-02T03:04:05.678Z";
  wireApi({
    "grant-list-mine": () => ({
      grants: [
        grant({ id: "g_0000000000000001", status: "requested", requestedAt }),
        grant({ id: "g_0000000000000002", status: "approved", expiresAt: "2025-01-31T16:00:00.000Z" }),
        grant({ id: "g_0000000000000003", status: "rejected" }),
        grant({ id: "g_0000000000000004", status: "revoked" }),
        grant({ id: "g_0000000000000005", status: "approved", expired: true, expiresAt: "2024-12-01T00:00:00.000Z" }),
      ],
    }),
  });
  render(<ProfileView />);

  const list = await screen.findByRole("list", { name: "사용 권한 신청 내역" });
  const rows = within(list).getAllByRole("listitem");
  expect(rows).toHaveLength(5);
  const labels = rows.map((row) => row.querySelector(".badge")?.textContent);
  expect(labels).toEqual(["대기", "승인", "거절", "폐기", "만료"]);

  expect(within(rows[0]).getByText(`신청 ${formatKst(requestedAt)}`)).toBeInTheDocument();
  // 2025-01-31T16:00Z is already 2025-02-01 in KST.
  expect(within(rows[1]).getByText("만료일 2025-02-01")).toBeInTheDocument();
  expect(within(rows[2]).queryByText(/만료일/)).toBeNull();
});

test("a pending request disables the form and shows the guidance", async () => {
  wireApi({
    "grant-list-mine": () => ({ grants: [grant({ status: "requested", purpose: "대기 중 목적" })] }),
  });
  render(<ProfileView />);

  expect(await screen.findByText("승인 대기 중인 신청이 있습니다.")).toBeInTheDocument();
  expect(field("사용 목적")).toBeDisabled();
  expect(screen.getByRole("button", { name: "사용 권한 신청" })).toBeDisabled();
});

test("without a pending request the form stays enabled and has no guidance", async () => {
  wireApi({ "grant-list-mine": () => ({ grants: [grant({ status: "rejected" })] }) });
  render(<ProfileView />);

  await screen.findByText("거절");
  expect(field("사용 목적")).toBeEnabled();
  expect(screen.getByRole("button", { name: "사용 권한 신청" })).toBeEnabled();
  expect(screen.queryByText("승인 대기 중인 신청이 있습니다.")).toBeNull();
});

test("an empty grant list shows the guidance", async () => {
  wireApi();
  render(<ProfileView />);

  expect(await screen.findByText("아직 신청한 사용 권한이 없습니다.")).toBeInTheDocument();
  expect(screen.queryByRole("list", { name: "사용 권한 신청 내역" })).toBeNull();
});

test("a grant list failure shows an alert inside the section and keeps the rest of the page", async () => {
  wireApi({
    "grant-list-mine": () => {
      throw new ApiRequestError(500, "internal_error", "권한 목록 오류");
    },
  });
  render(<ProfileView />);

  const section = await screen.findByRole("region", { name: "Bedrock 사용 권한" });
  expect(await within(section).findByRole("alert")).toHaveTextContent("권한 목록 오류");
  expect(within(section).queryByText("아직 신청한 사용 권한이 없습니다.")).toBeNull();
  expect(await screen.findByText("앨리스")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "비밀번호 변경" })).toBeInTheDocument();
});

test("a failed grant request shows the backend message in the section", async () => {
  wireApi({
    "grant-request": () => {
      throw new ApiRequestError(400, "bad_request", "이미 승인 대기 중인 신청이 있습니다.");
    },
  });
  render(<ProfileView />);
  await screen.findByText("아직 신청한 사용 권한이 없습니다.");

  fireEvent.submit(grantForm());

  const section = screen.getByRole("region", { name: "Bedrock 사용 권한" });
  expect(await within(section).findByRole("alert")).toHaveTextContent("이미 승인 대기 중인 신청이 있습니다.");
});

test("a grant request failure without a backend message shows the Korean fallback", async () => {
  wireApi({
    "grant-request": () => {
      throw new TypeError("Failed to fetch");
    },
  });
  render(<ProfileView />);
  await screen.findByText("아직 신청한 사용 권한이 없습니다.");

  fireEvent.submit(grantForm());

  expect(
    await screen.findByText("사용 권한을 신청하지 못했습니다. 잠시 후 다시 시도하세요."),
  ).toBeInTheDocument();
});
