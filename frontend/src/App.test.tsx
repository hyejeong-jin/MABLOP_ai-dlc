import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { App } from "./App";
import * as api from "./api";
import { SESSION_KEY } from "./auth/session";

function seedSession(role: "admin" | "member") {
  localStorage.setItem(
    SESSION_KEY,
    JSON.stringify({
      token: "t",
      expiresAt: new Date(Date.now() + 3600_000).toISOString(),
      user: { loginId: "user1", name: "사용자", role },
    }),
  );
}

// Views call the API on mount; answer with empty data so no network is used.
function mockApi() {
  return vi.spyOn(api, "postJson").mockImplementation((async (body: Record<string, unknown>) => {
    if (body.action === "get-learning-status") return { processedPosts: 0, chunks: 0, posts: [] };
    if (body.action === "list-research-runs") return { runs: [] };
    return { grants: [], drafts: [] };
  }) as never);
}

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

// Smoke test: without a session only the login screen is shown.
test("shows the login view and no tabs when there is no session", () => {
  mockApi();
  render(<App />);
  expect(screen.getByRole("heading", { name: "MABLOP" })).toBeInTheDocument();
  expect(screen.queryByRole("navigation", { name: "주요 기능" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "스타일 학습" })).not.toBeInTheDocument();
});

// Smoke test: with a valid session the app renders the style-learning view by default.
test("renders app and the research view by default with a session", () => {
  seedSession("member");
  mockApi();
  render(<App />);
  expect(screen.getByRole("heading", { name: "MABLOP" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "글 재료 수집" })).toBeInTheDocument();
});

test("hides the 관리 tab from members and shows it to admins", () => {
  seedSession("member");
  mockApi();
  const { unmount } = render(<App />);
  expect(screen.getByRole("button", { name: "마이페이지" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "관리" })).not.toBeInTheDocument();
  unmount();

  localStorage.clear();
  seedSession("admin");
  render(<App />);
  expect(screen.getByRole("button", { name: "관리" })).toBeInTheDocument();
});
