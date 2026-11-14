import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { ResearchSummary, safeExternalUrl } from "./ResearchSummary";

const sources = [
  { sourceId: "s1", title: "예시 글", domain: "example.com", url: "https://example.com/a" },
  { sourceId: "s2", domain: "evil.test", url: "javascript:alert(1)" },
];

describe("ResearchSummary", () => {
  test("fact shows source link safely; generated has no link", () => {
    render(
      <ResearchSummary
        sources={sources}
        summary={{
          facts: [{ text: "사실 문장", sourceIds: ["s1"] }],
          generatedInterpretation: [{ text: "해석 문장", sourceIds: ["s1"] }],
        }}
      />,
    );
    const link = screen.getByRole("link", { name: /예시 글/ });
    expect(link.getAttribute("href")).toBe("https://example.com/a");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(screen.getByText("생성")).toBeTruthy();
    expect(screen.getAllByRole("link")).toHaveLength(1);
  });

  test("fact without resolvable source is shown as unverified; unsafe url has no link", () => {
    render(
      <ResearchSummary
        sources={sources}
        summary={{
          facts: [
            { text: "출처 없음", sourceIds: [] },
            { text: "위험 링크", sourceIds: ["s2"] },
          ],
        }}
      />,
    );
    // Only the item with a resolvable source keeps the fact badge.
    expect(screen.getAllByText("사실")).toHaveLength(1);
    expect(screen.getAllByText("출처 미확인")).toHaveLength(1);
    expect(screen.queryAllByRole("link").length).toBe(0);
    expect(screen.getByText("evil.test")).toBeTruthy();
  });

  test("safeExternalUrl allows only http/https", () => {
    expect(safeExternalUrl("http://a.com")).toBe("http://a.com/");
    expect(safeExternalUrl("javascript:alert(1)")).toBeNull();
    expect(safeExternalUrl("data:text/html,x")).toBeNull();
    expect(safeExternalUrl("not a url")).toBeNull();
  });

  test("renders overview, sections with numbered source links, title ideas and keyword chips", () => {
    render(
      <ResearchSummary
        sources={sources}
        summary={{
          keyword: "k",
          overview: "전체 개요",
          sections: [
            {
              heading: "소제목 A",
              points: [
                { text: "항목 하나", sourceIds: ["s1"] },
                { text: "위험 항목", sourceIds: ["s2"] },
              ],
            },
          ],
          titleIdeas: ["제목 후보"],
          keywords: ["칩1", "칩2"],
          facts: [{ text: "레거시 사실", sourceIds: ["s1"] }],
        }}
      />,
    );
    expect(screen.getByText("전체 개요")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "소제목 A" })).toBeTruthy();
    const link = screen.getByRole("link", { name: "출처 1" });
    expect(link.getAttribute("href")).toBe("https://example.com/a");
    expect(screen.getAllByRole("link")).toHaveLength(1); // javascript: source [2] is not linked
    expect(screen.getByText("[2]")).toBeTruthy();
    expect(screen.getByText("제목 후보")).toBeTruthy();
    expect(screen.getByText("칩2")).toBeTruthy();
    expect(screen.queryByText("레거시 사실")).toBeNull();
  });
});