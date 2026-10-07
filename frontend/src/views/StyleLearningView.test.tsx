import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { StyleLearningView } from "./StyleLearningView";
import * as api from "../api";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

// renders the core fields
test("renders url, maxPosts and token fields", () => {
  render(<StyleLearningView />);
  expect(screen.getByLabelText("네이버 블로그 URL")).toBeInTheDocument();
  expect(screen.getByLabelText("최대 포스트 수")).toBeInTheDocument();
  expect(screen.getByLabelText("토큰")).toBeInTheDocument();
});

// typing + submit -> postJson called with learn-style payload; status + failedPosts rendered
test("submits learn-style payload and renders status and failedPosts", async () => {
  const postJson = vi.spyOn(api, "postJson").mockResolvedValue({
    runId: "run-abc",
    status: "completed",
    processedPosts: 12,
    chunks: 143,
    failedPosts: ["p4"],
  } as never);

  render(<StyleLearningView />);

  fireEvent.change(screen.getByLabelText("토큰"), { target: { value: "secret" } });
  fireEvent.change(screen.getByLabelText("네이버 블로그 URL"), {
    target: { value: "https://blog.naver.com/me" },
  });

  fireEvent.click(screen.getByRole("button", { name: "학습 시작" }));

  await waitFor(() => expect(postJson).toHaveBeenCalledTimes(1));
  const [body, token] = postJson.mock.calls[0];
  expect(token).toBe("secret");
  expect(body).toMatchObject({
    action: "learn-style",
    blogUrl: "https://blog.naver.com/me",
    maxPosts: 15,
  });

  expect(await screen.findByText("상태: completed")).toBeInTheDocument();
  expect(screen.getByText(/처리된 포스트: 12/)).toBeInTheDocument();
  expect(screen.getByText("p4")).toBeInTheDocument();
});

// in-progress response -> resume button appears
test("surfaces runId and allows resume on in-progress", async () => {
  vi.spyOn(api, "postJson").mockResolvedValue({
    runId: "run-xyz",
    status: "in-progress",
    checkpoint: { done: 5 },
  } as never);

  render(<StyleLearningView />);
  fireEvent.change(screen.getByLabelText("토큰"), { target: { value: "t" } });
  fireEvent.click(screen.getByRole("button", { name: "학습 시작" }));

  expect(await screen.findByText("runId: run-xyz")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "이어서 학습" })).toBeInTheDocument();
});
