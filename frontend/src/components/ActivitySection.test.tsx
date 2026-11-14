import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import * as api from "../api";
import { ActivitySection, dayLabel, levelOf, yearDates } from "./ActivitySection";

// Use days inside the displayed window (the last 365 days ending today, KST).
const DAYS = yearDates();
const A = DAYS[360];
const B = DAYS[361];

const response = {
  events: [],
  daily: [
    { date: A, total: 3, byType: { draft: 2, learned: 1 }, eventIds: ["d1", "d2", "l1"] },
    { date: B, total: 1, byType: { draft: 1 }, eventIds: ["d3"] },
  ],
  range: { from: DAYS[0], to: DAYS[364] },
};
afterEach(() => vi.restoreAllMocks());

describe("ActivitySection calendar", () => {
  test("requests one year of day buckets and renders a cell per day", async () => {
    const request = vi.spyOn(api, "postJson").mockResolvedValue(response as never);
    render(<ActivitySection />);

    const cells = await screen.findAllByRole("button", { name: /^\d{4}-\d{2}-\d{2} · 초안/ });
    expect(cells).toHaveLength(365);
    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({ action: "get-activity", granularity: "day" }),
    );
    const body = request.mock.calls[0][0] as { from: string; to: string };
    expect(body.from < body.to).toBe(true);
    expect(screen.getByRole("group", { name: "최근 1년 활동 달력" })).toBeInTheDocument();
  });

  test("cell label shows the date with draft and learned counts", async () => {
    vi.spyOn(api, "postJson").mockResolvedValue(response as never);
    render(<ActivitySection />);

    const cell = await screen.findByRole("button", { name: `${A} · 초안 2회 · 학습 1회` });
    expect(cell).toHaveAttribute("title", `${A} · 초안 2회 · 학습 1회`);
    expect(cell).toHaveStyle({ background: "var(--heat-3)" });
  });

  test("hover, focus and click show the same day summary", async () => {
    vi.spyOn(api, "postJson").mockResolvedValue(response as never);
    render(<ActivitySection />);
    const cell = await screen.findByRole("button", { name: `${B} · 초안 1회 · 학습 0회` });

    fireEvent.pointerEnter(cell);
    expect(screen.getByRole("status")).toHaveTextContent(`${B} · 초안 1회 · 학습 0회`);

    fireEvent.pointerLeave(cell);
    expect(screen.getByRole("status")).toHaveTextContent("날짜 칸에 마우스를 올리거나 누르면");

    fireEvent.focus(cell);
    expect(screen.getByRole("status")).toHaveTextContent(B);
    fireEvent.blur(cell);

    fireEvent.click(cell);
    expect(cell).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("status")).toHaveTextContent(`${B} · 초안 1회`);
  });

  test("legend is text-labelled and summary counts the year's activity", async () => {
    vi.spyOn(api, "postJson").mockResolvedValue(response as never);
    render(<ActivitySection />);

    const legend = await screen.findByLabelText("활동량 범례");
    expect(legend).toHaveTextContent("적음");
    expect(legend).toHaveTextContent("많음");
    expect(screen.getByText("4").closest("p")).toHaveTextContent("최근 1년 중 2일 활동 · 총 4회");
  });

  test("shows the error alert when the request fails", async () => {
    vi.spyOn(api, "postJson").mockRejectedValue(new Error("boom"));
    render(<ActivitySection />);
    expect(await screen.findByRole("alert")).toHaveTextContent("활동을 불러오지 못했습니다.");
  });
});

describe("activity helpers", () => {
  test("yearDates returns 365 consecutive KST days ending today", () => {
    const dates = yearDates(new Date("2026-11-14T03:00:00Z"));
    expect(dates).toHaveLength(365);
    expect(dates[364]).toBe("2026-11-14");
    expect(dates[0]).toBe("2025-11-15");
  });

  test("levelOf uses 0, 1, 2, 3-4 and 5+ thresholds", () => {
    expect([0, 1, 2, 3, 4, 5, 9].map(levelOf)).toEqual([0, 1, 2, 3, 3, 4, 4]);
  });

  test("dayLabel names draft and learned counts first", () => {
    expect(dayLabel("2025-01-02", { learned: 1, draft: 2 })).toBe("2025-01-02 · 초안 2회 · 학습 1회");
    expect(dayLabel("2025-01-03")).toBe("2025-01-03 · 초안 0회 · 학습 0회");
  });
});