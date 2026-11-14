import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { SourceLabel } from "./SourceLabel";

describe("SourceLabel", () => {
  test.each([
    ["fact", "사실"],
    ["generated", "생성"],
    ["user-input", "사용자 입력"],
    ["unverified", "출처 미확인"],
  ] as const)("%s shows text badge and description", (kind, text) => {
    const { container } = render(<SourceLabel kind={kind} />);
    expect(screen.getByText(text)).toBeTruthy();
    expect(container.querySelector(`.source-label-${kind}`)).toBeTruthy();
    expect(container.querySelector(".source-meta")?.textContent?.length).toBeGreaterThan(0);
  });
});
