import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import * as api from "../api";
import { ActivitySection } from "./ActivitySection";

const response = {
  events: [
    {
      eventId: "evt-voc",
      actorUserId: "acting-user",
      type: "voc-created",
      occurredAt: "2025-01-10T01:00:00Z",
      count: 2,
      relatedVocId: "voc-123",
      relatedRunId: null,
      metadata: {},
    },
    {
      eventId: "evt-research",
      actorUserId: "acting-user",
      type: "research-completed",
      occurredAt: "2025-01-11T02:00:00Z",
      count: 1,
      relatedVocId: null,
      relatedRunId: "run-456",
      metadata: {},
    },
  ],
  daily: [
    { date: "2025-01-10", total: 2, byType: { "voc-created": 2 }, eventIds: ["evt-voc"] },
    { date: "2025-01-11", total: 1, byType: { "research-completed": 1 }, eventIds: ["evt-research"] },
  ],
  range: { from: "2025-01-01T00:00:00+09:00", to: "2025-01-31T00:00:00+09:00" },
};

afterEach(() => vi.restoreAllMocks());

describe("ActivitySection", () => {
  test("renders only the API response events with text legend and accessible names", async () => {
    const request = vi.spyOn(api, "postJson").mockResolvedValue(response);

    render(<ActivitySection />);

    const point = (await screen.findAllByRole("button", { name: /VOC 작성/ }))[0];
    expect(point).toHaveAttribute(
      "aria-label",
      expect.stringContaining("2회"),
    );
    expect(screen.getByRole("group", { name: "활동 시각화" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "활동 스캐터 플롯" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "일별 활동 히트맵" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "일별 활동 히트맵" })).toHaveTextContent("2");
    const legend = screen.getByText("적음").parentElement;
    expect(legend).toHaveAttribute("aria-label", "활동량 범례");
    expect(legend).toHaveTextContent("많음");
    expect(request).toHaveBeenCalledWith(expect.objectContaining({ action: "get-activity" }));
  });

  test.each([
    ["pointer hover", (button: HTMLElement) => fireEvent.pointerEnter(button)],
    ["keyboard focus", (button: HTMLElement) => fireEvent.focus(button)],
    ["Enter", (button: HTMLElement) => fireEvent.keyDown(button, { key: "Enter" })],
    ["Space", (button: HTMLElement) => fireEvent.keyDown(button, { key: " " })],
    ["touch click", (button: HTMLElement) => fireEvent.click(button)],
  ])("opens the same detail model via %s", async (_input, activate) => {
    vi.spyOn(api, "postJson").mockResolvedValue(response);
    render(<ActivitySection />);
    const point = (await screen.findAllByRole("button", { name: /VOC 작성/ }))[0];

    activate(point);

    expect((await screen.findAllByText("VOC 작성", { selector: "strong" }))[0]).toBeInTheDocument();
    expect(screen.getByText("voc-123")).toBeInTheDocument();
    expect(screen.getAllByText("2회").length).toBeGreaterThanOrEqual(1);
  });

  test("closes a selected detail and shows the empty-state guidance", async () => {
    vi.spyOn(api, "postJson").mockResolvedValue({ ...response, events: [], daily: [] });
    render(<ActivitySection />);

    expect(await screen.findByText("선택한 기간에 활동이 없습니다.")).toBeInTheDocument();
    expect(screen.getByText("기간을 늘려 다른 활동이 있는지 확인해 보세요.")).toBeInTheDocument();
  });
});

describe("ActivitySection draft/learned hover", () => {
  test("heatmap cell shows date with draft and learned counts on hover", async () => {
    vi.spyOn(api, "postJson").mockResolvedValue({
      events: [
        { eventId: "draft-1", type: "draft", occurredAt: "2025-01-10T01:00:00Z", count: 1, metadata: {} },
        { eventId: "draft-2", type: "draft", occurredAt: "2025-01-10T02:00:00Z", count: 1, metadata: {} },
        { eventId: "learned-1", type: "learned", occurredAt: "2025-01-10T03:00:00Z", count: 1, metadata: {} },
      ],
      daily: [{ date: "2025-01-10", total: 3, byType: { draft: 2, learned: 1 }, eventIds: ["draft-1", "draft-2", "learned-1"] }],
      range: { from: "2025-01-01T00:00:00+09:00", to: "2025-01-31T00:00:00+09:00" },
    } as never);
    render(<ActivitySection />);
    const cell = await screen.findByRole("button", { name: "2025-01-10 · 초안 2회 · 학습 1회" });
    expect(cell).toHaveAttribute("title", "2025-01-10 · 초안 2회 · 학습 1회");
    fireEvent.pointerEnter(cell);
    expect((await screen.findAllByText("2025-01-10 · 초안 2회 · 학습 1회")).length).toBeGreaterThan(0);
  });
});
