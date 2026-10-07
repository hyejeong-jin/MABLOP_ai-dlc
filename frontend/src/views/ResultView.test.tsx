import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ResultView } from "./ResultView";

afterEach(() => {
  vi.restoreAllMocks();
});

// loading -> single synchronous indicator (Req 2.9)
test("renders synchronous loading indicator while loading", () => {
  render(<ResultView loading={true} />);
  expect(screen.getByText("초안 생성 중...")).toBeInTheDocument();
  // no output yet
  expect(screen.queryByLabelText("생성된 마크다운")).not.toBeInTheDocument();
});

// markdown present -> rendered in output + copy button present
test("renders returned markdown and a copy control", async () => {
  const md = "# 제목\n\n본문 ![img-0]";
  const writeText = vi.fn().mockResolvedValue(undefined);
  // native clipboard
  Object.assign(navigator, { clipboard: { writeText } });

  render(<ResultView loading={false} markdown={md} />);

  const output = screen.getByLabelText("생성된 마크다운");
  expect(output).toHaveTextContent("# 제목");
  expect(output).toHaveTextContent("![img-0]");

  const copyBtn = screen.getByRole("button", { name: "복사" });
  expect(copyBtn).toBeInTheDocument();

  fireEvent.click(copyBtn);
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(md));
  expect(await screen.findByText("복사됨")).toBeInTheDocument();
});

// error present -> surfaced
test("renders error when present", () => {
  render(<ResultView loading={false} error="502: bedrock down" />);
  expect(screen.getByRole("alert")).toHaveTextContent("502: bedrock down");
});
