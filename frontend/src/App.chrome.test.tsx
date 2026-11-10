// App chrome: welcome guide after login, bell (update notes), help (manual), account chip.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";
import { SESSION_KEY } from "./auth/session";

const HIDE_KEY = "mablop-manual-hidden-until:user1";

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem(
    SESSION_KEY,
    JSON.stringify({
      token: "t",
      expiresAt: new Date(Date.now() + 3600_000).toISOString(),
      user: { loginId: "user1", name: "사용자", role: "member" },
    }),
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init: { body: string }) => {
      const body = JSON.parse(init.body) as { action: string };
      const data =
        body.action === "get-learning-status"
          ? { processedPosts: 0, chunks: 0, posts: [] }
          : body.action === "grant-list-mine"
            ? { grants: [] }
            : {};
      return { ok: true, status: 200, json: async () => data };
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

test("the guide opens right after login and 하루 동안 보지 않기 keeps it closed for a day", () => {
  const { unmount } = render(<App />);
  const guide = screen.getByRole("dialog", { name: "사용자 매뉴얼" });
  expect(guide).toBeInTheDocument();

  fireEvent.click(screen.getByRole("checkbox", { name: "하루 동안 보지 않기" }));
  fireEvent.click(screen.getByRole("button", { name: "확인했어요" }));
  expect(screen.queryByRole("dialog")).toBeNull();

  const until = Number(localStorage.getItem(HIDE_KEY));
  expect(until).toBeGreaterThan(Date.now() + 23 * 3600_000);
  expect(until).toBeLessThanOrEqual(Date.now() + 24 * 3600_000);

  unmount();
  render(<App />);
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("closing the guide without the checkbox shows it again on the next login", () => {
  const { unmount } = render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "확인했어요" }));
  expect(localStorage.getItem(HIDE_KEY)).toBeNull();
  unmount();
  render(<App />);
  expect(screen.getByRole("dialog", { name: "사용자 매뉴얼" })).toBeInTheDocument();
});

test("once the day has passed the guide comes back", () => {
  localStorage.setItem(HIDE_KEY, String(Date.now() - 1000));
  render(<App />);
  expect(screen.getByRole("dialog", { name: "사용자 매뉴얼" })).toBeInTheDocument();
});

test("the bell opens the update notes and the question mark opens only the manual", () => {
  localStorage.setItem(HIDE_KEY, String(Date.now() + 3600_000));
  render(<App />);
  expect(screen.queryByRole("dialog")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "업데이트 노트" }));
  expect(screen.getByRole("dialog", { name: "업데이트 노트" })).toBeInTheDocument();
  expect(screen.getByText("MABLOP 서비스 오픈")).toBeInTheDocument();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "사용자 매뉴얼" }));
  expect(screen.getByRole("dialog", { name: "사용자 매뉴얼" })).toBeInTheDocument();
  // opened by hand: no "do not show today" checkbox
  expect(screen.queryByRole("checkbox", { name: "하루 동안 보지 않기" })).toBeNull();
  // the manual does not contain the update notes
  expect(screen.queryByText("MABLOP 서비스 오픈")).toBeNull();
});

test("the bell shows an unread dot until the notes were opened", () => {
  localStorage.setItem(HIDE_KEY, String(Date.now() + 3600_000));
  const { container } = render(<App />);
  expect(container.querySelector(".notify-dot")).not.toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "업데이트 노트" }));
  expect(container.querySelector(".notify-dot")).toBeNull();
});

test("the user id sits in the bottom-left account chip and the three header buttons share one class", () => {
  localStorage.setItem(HIDE_KEY, String(Date.now() + 3600_000));
  render(<App />);
  const chip = screen.getByRole("button", { name: "user1 계정 메뉴" });
  expect(chip).toHaveTextContent("user1");
  expect(chip.closest(".account-dock")).not.toBeNull();
  for (const name of ["업데이트 노트", "다크 모드로 전환", "사용자 매뉴얼"]) {
    expect(screen.getByRole("button", { name }).className).toContain("icon-button");
  }
  expect(screen.getByRole("link", { name: "hyejeong.data@gmail.com" })).toBeInTheDocument();
});

test("the top tabs are a single row of five or four buttons without a scroll container", () => {
  localStorage.setItem(HIDE_KEY, String(Date.now() + 3600_000));
  render(<App />);
  const nav = screen.getByRole("navigation", { name: "주요 기능" });
  expect(nav.querySelectorAll("button")).toHaveLength(4);
  expect(screen.getByRole("button", { name: "마이페이지" })).toBeInTheDocument();
});