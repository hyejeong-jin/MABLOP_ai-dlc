// Account chip menu (task 14.1): the bottom-left user id opens 마이페이지 and 로그아웃.
// Uses the real App, AuthContext and session storage; only fetch is stubbed.
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
  // Keep the first-visit guide closed so it does not cover the account dock.
  localStorage.setItem(HIDE_KEY, String(Date.now() + 3600_000));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init: { body: string }) => {
      const body = JSON.parse(init.body) as { action: string };
      const data =
        body.action === "get-me"
          ? { loginId: "user1", name: "사용자" }
          : body.action === "get-learning-status"
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

function openAccountMenu() {
  fireEvent.click(screen.getByRole("button", { name: "user1 계정 메뉴" }));
  return screen.getByRole("menu");
}

test("clicking the account chip opens a menu with 마이페이지 and 로그아웃", () => {
  render(<App />);
  expect(screen.queryByRole("menu")).toBeNull();

  const chip = screen.getByRole("button", { name: "user1 계정 메뉴" });
  const menu = openAccountMenu();

  expect(chip).toHaveAttribute("aria-expanded", "true");
  expect(menu).toBeVisible();
  const items = screen.getAllByRole("menuitem");
  expect(items.map((item) => item.textContent)).toEqual(["마이페이지", "로그아웃"]);
});

test("the chip toggles the menu closed when pressed again", () => {
  render(<App />);
  openAccountMenu();
  fireEvent.click(screen.getByRole("button", { name: "user1 계정 메뉴" }));
  expect(screen.queryByRole("menu")).toBeNull();
  expect(screen.getByRole("button", { name: "user1 계정 메뉴" })).toHaveAttribute("aria-expanded", "false");
});

test("a press outside the menu or an Escape key closes it", () => {
  render(<App />);
  openAccountMenu();
  fireEvent.mouseDown(document.body);
  expect(screen.queryByRole("menu")).toBeNull();

  openAccountMenu();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("menu")).toBeNull();
});

test("마이페이지 in the menu switches to the profile view and closes the menu", async () => {
  render(<App />);
  openAccountMenu();
  fireEvent.click(screen.getByRole("menuitem", { name: "마이페이지" }));

  expect(screen.queryByRole("menu")).toBeNull();
  expect(await screen.findByRole("heading", { name: "마이페이지" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "로그아웃" })).toBeInTheDocument();
});

test("로그아웃 in the menu logs the user out and returns to the login screen", () => {
  render(<App />);
  openAccountMenu();
  fireEvent.click(screen.getByRole("menuitem", { name: "로그아웃" }));

  expect(localStorage.getItem(SESSION_KEY)).toBeNull();
  expect(screen.getByRole("button", { name: "로그인" })).toBeInTheDocument();
  expect(screen.queryByRole("navigation", { name: "주요 기능" })).toBeNull();
});
