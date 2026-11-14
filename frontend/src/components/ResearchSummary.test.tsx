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
});
