import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import * as api from "../api";
import { ManualDialog } from "./ManualDialog";
import { UpdateNotesDialog } from "./UpdateNotesDialog";
import { UsageDialog } from "./UsageDialog";

afterEach(() => vi.restoreAllMocks());

test("UsageDialog shows month total, per-model rows and the reset time", async () => {
  vi.spyOn(api, "postJson").mockResolvedValue({
    month: "2026-11",
    totalUsd: 0.0121,
    resetAt: "2026-11-30T15:00:00.000Z",
    models: [
      { modelId: "m1", label: "Claude Sonnet 4.5", calls: 4, inputTokens: 17351, outputTokens: 2973, usd: 0.012134, priced: true },
    ],
    pricing: [{ label: "Claude Sonnet 4.5", inputPerMillion: 3, outputPerMillion: 15 }],
  } as never);
  render(<UsageDialog onClose={() => {}} />);

  expect(await screen.findByText("$0.0121")).toBeInTheDocument();
  expect(screen.getAllByText("Claude Sonnet 4.5").length).toBeGreaterThan(0);
  expect(screen.getByText(/4회 · 입력 17,351/)).toBeInTheDocument();
  expect(screen.getByText(/다음 초기화/)).toBeInTheDocument();
  expect(api.postJson).toHaveBeenCalledWith({ action: "get-usage" });
});

test("UsageDialog shows an empty-month message and the backend error", async () => {
  vi.spyOn(api, "postJson").mockResolvedValueOnce({
    month: "2026-11", totalUsd: 0, resetAt: "2026-11-30T15:00:00.000Z", models: [], pricing: [],
  } as never);
  const { unmount } = render(<UsageDialog onClose={() => {}} />);
  expect(await screen.findByText("이번 달에는 아직 사용 기록이 없습니다.")).toBeInTheDocument();
  unmount();

  vi.spyOn(api, "postJson").mockRejectedValueOnce(new api.ApiRequestError(500, "internal_error", "서버 오류"));
  render(<UsageDialog onClose={() => {}} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("서버 오류");
});

test("dialogs close with the close button and with Escape", async () => {
  const onClose = vi.fn();
  render(<ManualDialog onClose={onClose} />);
  expect(screen.getByRole("dialog", { name: "사용자 매뉴얼" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "닫기" }));
  fireEvent.keyDown(document, { key: "Escape" });
  await waitFor(() => expect(onClose).toHaveBeenCalledTimes(2));
});

test("UpdateNotesDialog lists releases newest first", () => {
  render(<UpdateNotesDialog onClose={() => {}} />);
  const versions = screen.getAllByText(/^v\d+\.\d+\.\d+$/).map((el) => el.textContent);
  expect(versions[0]).toBe("v1.2.0");
  expect(versions).toContain("v1.0.0");
});