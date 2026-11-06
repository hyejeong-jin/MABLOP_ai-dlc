import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { StyleLearningView } from "./StyleLearningView";
import * as api from "../api";

// The view reads the session user through useAuth(); the tests choose the role.
const auth = vi.hoisted(() => ({
  user: null as { loginId: string; name: string; role: "member" | "admin" } | null,
}));
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    user: auth.user,
    notice: null,
    login: async () => {},
    register: async () => "",
    logout: () => {},
    replaceToken: () => {},
  }),
}));

const GRANT = {
  id: "g1",
  status: "approved",
  purpose: null,
  requestedAt: "2025-01-01T00:00:00Z",
  decidedAt: "2025-01-02T00:00:00Z",
  expiresAt: "2025-02-01T00:00:00Z",
  expired: false,
};

type Handler = (body: Record<string, unknown>) => unknown;

// Routes postJson by action; grant-list-mine returns one usable Grant by default.
function mockApi(handlers: Record<string, Handler> = {}) {
  return vi.spyOn(api, "postJson").mockImplementation((async (body: Record<string, unknown>) => {
    const handler = handlers[body.action as string];
    if (handler) return handler(body);
    if (body.action === "grant-list-mine") return { grants: [GRANT] };
    if (body.action === "get-learning-status") return { processedPosts: 0, chunks: 0, posts: [] };
    throw new Error(`unexpected action ${String(body.action)}`);
  }) as never);
}

function callsFor(spy: ReturnType<typeof mockApi>, action: string) {
  return spy.mock.calls.filter(([body]) => body.action === action);
}

async function selectGrant() {
  await screen.findByRole("option", { name: /^Bedrock 사용 권한 · / });
  fireEvent.change(screen.getByLabelText("Bedrock 사용 권한"), { target: { value: "g1" } });
}

beforeEach(() => {
  auth.user = { loginId: "member1", name: "멤버", role: "member" };
});

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

// renders the core fields; the token field is gone and the Grant dropdown is shown
test("renders url, maxPosts and grant select without a token field", async () => {
  mockApi();
  render(<StyleLearningView />);
  expect(screen.getByLabelText("블로그 URL")).toBeInTheDocument();
  expect(screen.getByLabelText("최대 포스트 수")).toBeInTheDocument();
  expect(screen.queryByLabelText("토큰")).not.toBeInTheDocument();
  expect(await screen.findByLabelText("Bedrock 사용 권한")).toBeInTheDocument();
});

// typing + submit -> postJson called with learn-style payload + grantId; status + failedPosts rendered
test("submits learn-style payload with grantId and renders status and failedPosts", async () => {
  const postJson = mockApi({
    "learn-style": () => ({
      runId: "run-abc",
      status: "completed",
      processedPosts: 12,
      chunks: 143,
      failedPosts: ["p4"],
    }),
  });

  render(<StyleLearningView />);

  await selectGrant();
  fireEvent.change(screen.getByLabelText("블로그 URL"), {
    target: { value: "https://blog.naver.com/me" },
  });

  fireEvent.click(screen.getByRole("button", { name: "학습 시작" }));

  await waitFor(() => expect(callsFor(postJson, "learn-style")).toHaveLength(1));
  const call = callsFor(postJson, "learn-style")[0];
  expect(call).toHaveLength(1); // no token argument; the session token is attached by api.ts
  expect(call[0]).toMatchObject({
    action: "learn-style",
    blogUrl: "https://blog.naver.com/me",
    maxPosts: 15,
    grantId: "g1",
  });

  // get-learning-status is not a Bedrock action
  expect(callsFor(postJson, "get-learning-status")[0][0]).not.toHaveProperty("grantId");

  expect(await screen.findByText("상태: completed")).toBeInTheDocument();
  expect(screen.getByText(/처리된 포스트: 12/)).toBeInTheDocument();
  expect(screen.getByText("p4")).toBeInTheDocument();
});

// in-progress response -> resume button appears
test("surfaces runId and allows resume on in-progress", async () => {
  mockApi({
    "learn-style": () => ({ runId: "run-xyz", status: "in-progress", checkpoint: { done: 5 } }),
  });

  render(<StyleLearningView />);
  await selectGrant();
  fireEvent.click(screen.getByRole("button", { name: "학습 시작" }));

  expect(await screen.findByText("runId: run-xyz")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "이어서 학습" })).toBeInTheDocument();
});

// a member with no selected Grant cannot start learning
test("disables the learn button until a Grant is selected", async () => {
  const postJson = mockApi();
  render(<StyleLearningView />);
  await screen.findByRole("option", { name: /^Bedrock 사용 권한 · / });

  const button = screen.getByRole("button", { name: "학습 시작" });
  expect(button).toBeDisabled();
  fireEvent.click(button);
  expect(callsFor(postJson, "learn-style")).toHaveLength(0);

  fireEvent.change(screen.getByLabelText("Bedrock 사용 권한"), { target: { value: "g1" } });
  expect(screen.getByRole("button", { name: "학습 시작" })).toBeEnabled();
});

// a member with no usable Grant sees the empty notice and a disabled button
test("disables the learn button when there are no Grant options", async () => {
  mockApi({ "grant-list-mine": () => ({ grants: [] }) });
  render(<StyleLearningView />);
  expect(await screen.findByText(/사용 가능한 Bedrock 사용 권한이 없습니다/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "학습 시작" })).toBeDisabled();
});

// an admin has no dropdown, is never blocked, and sends no grantId
test("admin sees no grant select and learn-style carries no grantId", async () => {
  auth.user = { loginId: "admin", name: "관리자", role: "admin" };
  const postJson = mockApi({
    "learn-style": () => ({ runId: "r1", status: "completed", processedPosts: 1, chunks: 2 }),
  });
  render(<StyleLearningView />);

  expect(screen.queryByLabelText("Bedrock 사용 권한")).not.toBeInTheDocument();
  const button = screen.getByRole("button", { name: "학습 시작" });
  expect(button).toBeEnabled();
  fireEvent.click(button);

  await waitFor(() => expect(callsFor(postJson, "learn-style")).toHaveLength(1));
  expect(callsFor(postJson, "learn-style")[0][0]).not.toHaveProperty("grantId");
  expect(callsFor(postJson, "grant-list-mine")).toHaveLength(0);
});

// 403 forbidden -> message in the error area and the Grant options are reloaded
test("shows the backend message on 403 forbidden and reloads Grant options", async () => {
  const postJson = mockApi({
    "learn-style": () => {
      throw new api.ApiRequestError(403, "forbidden", "사용 권한이 만료되었습니다.");
    },
  });
  render(<StyleLearningView />);
  await selectGrant();
  expect(callsFor(postJson, "grant-list-mine")).toHaveLength(1);

  fireEvent.click(screen.getByRole("button", { name: "학습 시작" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("사용 권한이 만료되었습니다.");
  await waitFor(() => expect(callsFor(postJson, "grant-list-mine")).toHaveLength(2));
});

// preview is not a Bedrock action: it needs the URL only and sends no grantId
test("preview needs only the URL and sends no grantId", async () => {
  const postJson = mockApi({
    "preview-learning-sources": () => ({ count: 1, posts: [{ postId: "p1", title: "첫 글" }] }),
  });
  render(<StyleLearningView />);
  await screen.findByRole("option", { name: /^Bedrock 사용 권한 · / });

  fireEvent.change(screen.getByLabelText("블로그 URL"), {
    target: { value: "https://blog.naver.com/me" },
  });
  fireEvent.click(screen.getByRole("button", { name: "공개 포스트 미리보기" }));

  expect(await screen.findByText(/첫 글/)).toBeInTheDocument();
  const call = callsFor(postJson, "preview-learning-sources")[0];
  expect(call).toHaveLength(1);
  expect(call[0]).not.toHaveProperty("grantId");
});
