import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import * as api from "../api";
import { ManualDialog } from "./ManualDialog";
import { UpdateNotesDialog } from "./UpdateNotesDialog";
import { UsageSection, heatCells, heatLevel } from "./UsageSection";

afterEach(() => vi.restoreAllMocks());

test("UsageSection shows month total, per-model rows and the reset time", async () => {
  vi.spyOn(api, "postJson").mockResolvedValue({
    month: "2026-11",
    totalUsd: 0.0121,
    resetAt: "2026-11-30T15:00:00.000Z",
    models: [
      { modelId: "m1", label: "Claude Sonnet 4.5", calls: 4, inputTokens: 17351, outputTokens: 2973, usd: 0.012134, priced: true },
    ],
    pricing: [{ label: "Claude Sonnet 4.5", inputPerMillion: 3, outputPerMillion: 15 }],
  } as never);
  render(<UsageSection />);

  expect(await screen.findByText("$0.0121")).toBeInTheDocument();
  expect(screen.getAllByText("Claude Sonnet 4.5").length).toBeGreaterThan(0);
  expect(screen.getByText("4")).toBeInTheDocument();
  expect(screen.getByText("17,351")).toBeInTheDocument();
  expect(screen.getByText(/다음 초기화/)).toBeInTheDocument();
  expect(api.postJson).toHaveBeenCalledWith({ action: "get-usage" });
});

test("UsageSection shows an empty-month message and the backend error", async () => {
  vi.spyOn(api, "postJson").mockResolvedValueOnce({
    month: "2026-11", totalUsd: 0, resetAt: "2026-11-30T15:00:00.000Z", models: [], pricing: [],
  } as never);
  const { unmount } = render(<UsageSection />);
  expect(await screen.findByText("이번 달에는 아직 사용 기록이 없습니다.")).toBeInTheDocument();
  unmount();

  vi.spyOn(api, "postJson").mockRejectedValueOnce(new api.ApiRequestError(500, "internal_error", "서버 오류"));
  render(<UsageSection />);
  expect(await screen.findByRole("alert")).toHaveTextContent("서버 오류");
});

test("dialogs close with the close button and with Escape", async () => {
  const onClose = vi.fn();
  render(<ManualDialog onClose={onClose} />);
  expect(screen.getByRole("dialog", { name: "사용자 매뉴얼" })).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "닫기" })[0]);
  fireEvent.keyDown(document, { key: "Escape" });
  await waitFor(() => expect(onClose).toHaveBeenCalledTimes(2));
});

test("UpdateNotesDialog lists releases newest first", () => {
  render(<UpdateNotesDialog onClose={() => {}} />);
  const versions = screen.getAllByText(/^v\d+\.\d+\.\d+$/).map((el) => el.textContent);
  expect(versions[0]).toBe("v1.1.0");
  expect(versions[versions.length - 1]).toBe("v1.0.0");
  expect(screen.getByText("첫 릴리스")).toBeInTheDocument();
  expect(screen.getByText("MABLOP 서비스 오픈")).toBeInTheDocument();
});
test("welcome manual offers 하루 동안 보지 않기 and reports the choice when closed", () => {
  const onClose = vi.fn();
  const onHideToday = vi.fn();
  render(<ManualDialog welcome onClose={onClose} onHideToday={onHideToday} />);
  fireEvent.click(screen.getByRole("checkbox", { name: "하루 동안 보지 않기" }));
  fireEvent.click(screen.getByRole("button", { name: "확인했어요" }));
  expect(onHideToday).toHaveBeenCalledTimes(1);
  expect(onClose).toHaveBeenCalledTimes(1);
});

test("welcome manual without the checkbox does not hide the guide", () => {
  const onHideToday = vi.fn();
  render(<ManualDialog welcome onClose={() => {}} onHideToday={onHideToday} />);
  fireEvent.click(screen.getByRole("button", { name: "확인했어요" }));
  expect(onHideToday).not.toHaveBeenCalled();
});

test("manual shows a screen example with a description for every step", () => {
  render(<ManualDialog onClose={() => {}} />);
  const images = screen.getAllByRole("img");
  expect(images.length).toBe(7);
  for (const img of images) expect((img as HTMLImageElement).alt.length).toBeGreaterThan(10);
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(screen.getAllByText(/AI 서비스 토큰 사용 권한/).length).toBeGreaterThan(0);
});

test("heat map uses Korea dates, starts on a Sunday and darkens with activity", () => {
  // 2026-11-10 23:50 KST is still 11-10 in Korea but already 11-10 14:50 UTC; use a late-evening KST case
  const now = new Date("2026-11-10T15:30:00Z"); // 2026-11-11 00:30 KST
  const cells = heatCells({ "2026-11-11": 3, "2026-11-10": 1 }, now);
  const real = cells.filter((c) => c !== null);
  expect(real).toHaveLength(182);
  expect(real[real.length - 1]!.date).toBe("2026-11-11");
  expect(real[real.length - 1]!.count).toBe(3);
  expect(real[real.length - 2]!.count).toBe(1);
  const lead = cells.length - real.length;
  expect(new Date(`${real[0]!.date}T12:00:00+09:00`).getUTCDay()).toBe(lead);
  expect([0, 1, 2, 3, 4, 5, 9].map(heatLevel)).toEqual([0, 1, 2, 3, 3, 4, 4]);
});

test("UsageSection paints the days that have activity and shows cumulative tokens", async () => {
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul" }).format(new Date());
  vi.spyOn(api, "postJson").mockResolvedValue({
    month: "2026-11", totalUsd: 0, totalTokens: 123456, resetAt: "2026-11-30T15:00:00.000Z",
    models: [], pricing: [], activity: { [today]: 5 },
  } as never);
  const { container } = render(<UsageSection />);
  expect(await screen.findByText("123,456")).toBeInTheDocument();
  expect(container.querySelector(`.heatmap [title^="${today}"]`)?.className).toContain("level-4");
  expect(screen.getByText("포스트 작성 활동")).toBeInTheDocument();
});