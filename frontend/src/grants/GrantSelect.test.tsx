import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  GRANT_SELECT_EMPTY_MESSAGE,
  GRANT_SELECT_LABEL,
  GRANT_SELECT_PLACEHOLDER,
  GrantSelect,
} from "./GrantSelect";
import type { GrantOption, GrantsState } from "./useGrants";

function grant(id: string, overrides: Partial<GrantOption> = {}): GrantOption {
  return {
    id,
    status: "approved",
    purpose: null,
    requestedAt: "2025-01-01T00:00:00Z",
    // 2025-01-02T16:00Z is 2025-01-03 01:00 in KST.
    decidedAt: "2025-01-02T16:00:00Z",
    // 2025-12-31T15:30Z is 2026-01-01 00:30 in KST.
    expiresAt: "2025-12-31T15:30:00Z",
    expired: false,
    ...overrides,
  };
}

function state(overrides: Partial<GrantsState> = {}): GrantsState {
  return {
    enabled: true,
    options: [],
    selectedId: null,
    select: vi.fn(),
    reload: vi.fn(async () => {}),
    loading: false,
    error: null,
    canCallBedrock: false,
    withGrant: (body) => body,
    handleGrantError: () => false,
    ...overrides,
  };
}

describe("GrantSelect", () => {
  it("renders nothing for an admin (enabled=false)", () => {
    const { container } = render(<GrantSelect grants={state({ enabled: false })} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for an admin even when options exist", () => {
    const { container } = render(
      <GrantSelect grants={state({ enabled: false, options: [grant("g1")] })} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("ties the visible label 'Bedrock 사용 권한' to the select and offers '선택하세요' first", () => {
    render(<GrantSelect grants={state({ options: [grant("g1")] })} />);
    const select = screen.getByLabelText(GRANT_SELECT_LABEL);
    expect(GRANT_SELECT_LABEL).toBe("Bedrock 사용 권한");
    expect(select.tagName).toBe("SELECT");
    expect(screen.getByRole("combobox", { name: "Bedrock 사용 권한" })).toBe(select);
    const options = (select as HTMLSelectElement).options;
    expect(options[0].textContent).toBe("선택하세요");
    expect(GRANT_SELECT_PLACEHOLDER).toBe("선택하세요");
    expect(options[0].value).toBe("");
  });

  it("labels each option 'Bedrock 사용 권한 · 승인일 · 만료일' with KST YYYY-MM-DD dates", () => {
    render(
      <GrantSelect
        grants={state({
          options: [
            grant("g1"),
            grant("g2", {
              decidedAt: "2025-03-10T00:00:00Z",
              expiresAt: "2025-06-30T14:59:59Z",
            }),
          ],
        })}
      />,
    );
    const select = screen.getByLabelText(GRANT_SELECT_LABEL) as HTMLSelectElement;
    expect(select.options).toHaveLength(3);
    expect(select.options[1].value).toBe("g1");
    expect(select.options[1].textContent).toBe("Bedrock 사용 권한 · 2025-01-03 · 2026-01-01");
    expect(select.options[2].value).toBe("g2");
    expect(select.options[2].textContent).toBe("Bedrock 사용 권한 · 2025-03-10 · 2025-06-30");
  });

  it("reflects selectedId as the select value", () => {
    render(
      <GrantSelect
        grants={state({ options: [grant("g1"), grant("g2")], selectedId: "g2" })}
      />,
    );
    expect((screen.getByLabelText(GRANT_SELECT_LABEL) as HTMLSelectElement).value).toBe("g2");
  });

  it("shows the placeholder when nothing is selected", () => {
    render(<GrantSelect grants={state({ options: [grant("g1")], selectedId: null })} />);
    expect((screen.getByLabelText(GRANT_SELECT_LABEL) as HTMLSelectElement).value).toBe("");
  });

  it("calls select(id) when an option is chosen and select(null) for the placeholder", () => {
    const select = vi.fn();
    render(
      <GrantSelect
        grants={state({ options: [grant("g1"), grant("g2")], selectedId: "g1", select })}
      />,
    );
    const el = screen.getByLabelText(GRANT_SELECT_LABEL);
    fireEvent.change(el, { target: { value: "g2" } });
    expect(select).toHaveBeenLastCalledWith("g2");
    fireEvent.change(el, { target: { value: "" } });
    expect(select).toHaveBeenLastCalledWith(null);
    expect(select).toHaveBeenCalledTimes(2);
  });

  it("can receive keyboard focus and is not disabled when loaded", () => {
    render(<GrantSelect grants={state({ options: [grant("g1")] })} />);
    const el = screen.getByLabelText(GRANT_SELECT_LABEL);
    expect(el).toBeEnabled();
    expect(el).not.toHaveAttribute("tabindex", "-1");
    el.focus();
    expect(el).toHaveFocus();
  });

  it("shows the empty notice as role=status when there is no option and loading is done", () => {
    render(<GrantSelect grants={state({ options: [], loading: false })} />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(
      "사용 가능한 Bedrock 사용 권한이 없습니다. 마이페이지에서 신청하세요.",
    );
    expect(GRANT_SELECT_EMPTY_MESSAGE).toBe(
      "사용 가능한 Bedrock 사용 권한이 없습니다. 마이페이지에서 신청하세요.",
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not show the empty notice when options exist", () => {
    render(<GrantSelect grants={state({ options: [grant("g1")] })} />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByText(GRANT_SELECT_EMPTY_MESSAGE)).not.toBeInTheDocument();
  });

  it("disables the select and hides the empty notice while loading", () => {
    render(<GrantSelect grants={state({ options: [], loading: true })} />);
    expect(screen.getByLabelText(GRANT_SELECT_LABEL)).toBeDisabled();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByText(GRANT_SELECT_EMPTY_MESSAGE)).not.toBeInTheDocument();
  });

  it("shows an error as role=alert", () => {
    render(
      <GrantSelect
        grants={state({ options: [], error: "Bedrock 사용 권한을 불러오지 못했습니다." })}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Bedrock 사용 권한을 불러오지 못했습니다.",
    );
  });

  it("does not render an alert when there is no error", () => {
    render(<GrantSelect grants={state({ options: [grant("g1")] })} />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
