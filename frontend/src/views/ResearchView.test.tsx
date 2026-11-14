import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ResearchView } from "./ResearchView";

type Body = Record<string, unknown>;
let calls: Body[];
let runResponse: { status: number; data: unknown };

const okRun = {
  runId: "run_1",
  status: "completed",
  summary: { facts: [], generatedInterpretation: [], unverified: [], partial: false },
  sources: [
    { sourceId: "src_1", status: "succeeded", title: "A", url: "https://a.example/x" },
    { sourceId: "src_2", status: "failed", title: "B", errorMessage: "timeout" },
    { sourceId: "src_3", status: "skipped", title: "C" },
  ],
  usage: { requests: 3, inputChars: 100, outputTokens: 0, searchCalls: 1 },
  budget: { maxSearchCalls: 1 },
  cacheHit: false,
  controls: [],
};

beforeEach(() => {
  calls = [];
  runResponse = { status: 200, data: okRun };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init: { body: string }) => {
      const body = JSON.parse(init.body) as Body;
      calls.push(body);
      if (body.action === "list-research-runs") {
        return { ok: true, status: 200, json: async () => ({ runs: [{ runId: "run_0", status: "partial", requestedAt: "2025-01-10T03:00:00.000Z" }] }) };
      }
      return { ok: runResponse.status < 400, status: runResponse.status, json: async () => runResponse.data };
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

const fill = (n: number) => {
  for (let i = 0; i < n; i++) {
    fireEvent.change(screen.getByLabelText(`정보 수집 링크 ${i + 1}`), { target: { value: `https://site${i}.example/p` } });
  }
};
const submit = () => fireEvent.click(screen.getByRole("button", { name: "자료 수집하기" }));

test("안내문, 기본 3칸, 추가는 5개까지, 삭제는 3개까지", () => {
  render(<ResearchView />);
  expect(screen.getByRole("heading", { name: "글 재료 수집" })).toBeTruthy();
  expect(screen.getByText(/말투 학습에 쓰이지 않아요/)).toBeTruthy();
  expect(screen.getAllByLabelText(/정보 수집 링크 \d/)).toHaveLength(3);
  const del = () => screen.getAllByRole("button", { name: /^링크 \d 삭제$/ });
  expect((del()[0] as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "링크 추가" }));
  fireEvent.click(screen.getByRole("button", { name: "링크 추가" }));
  expect(screen.getAllByLabelText(/정보 수집 링크 \d/)).toHaveLength(5);
  expect((screen.getByRole("button", { name: "링크 추가" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(del()[0]);
  expect(screen.getAllByLabelText(/정보 수집 링크 \d/)).toHaveLength(4);
});

test("개수/형식 오류는 요청 없이 alert로 표시한다", async () => {
  render(<ResearchView />);
  submit();
  expect((await screen.findByRole("alert")).textContent).toMatch(/3개 이상/);
  fill(3);
  fireEvent.change(screen.getByLabelText("정보 수집 링크 2"), { target: { value: "ftp://x.example" } });
  submit();
  expect((await screen.findByRole("alert")).textContent).toMatch(/2번째 링크/);
  expect(calls.some((c) => c.action === "run-research")).toBe(false);
});

test("검색 꺼짐이면 search/budget을 보내지 않고, 켜면 보낸다", async () => {
  render(<ResearchView />);
  fill(3);
  submit();
  await screen.findByText(/실행 상태: 완료/);
  const first = calls.find((c) => c.action === "run-research")!;
  expect(first.search).toBeUndefined();
  expect(first.budget).toBeUndefined();
  expect(first.links).toHaveLength(3);

  fireEvent.click(screen.getByLabelText("웹 검색으로 보완하기"));
  expect(screen.getByText(/검색 1회당 추가 비용/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText("검색어 1"), { target: { value: "마케팅 트렌드" } });
  submit();
  await waitFor(() => expect(calls.filter((c) => c.action === "run-research")).toHaveLength(2));
  const second = calls.filter((c) => c.action === "run-research")[1];
  expect(second.search).toEqual({ enabled: true, queries: ["마케팅 트렌드"] });
  expect(second.budget).toEqual({ maxSearchCalls: 1 });
});

test("결과: 소스 상태 텍스트, 예산, 초안 버튼", async () => {
  const onUse = vi.fn();
  render(<ResearchView onUseInDraft={onUse} />);
  fill(3);
  submit();
  await screen.findByText(/실행 상태: 완료/);
  expect(screen.getByText("성공")).toBeTruthy();
  expect(screen.getByText("실패")).toBeTruthy();
  expect(screen.getByText("건너뜀")).toBeTruthy();
  expect(screen.getByText(/사유: timeout/)).toBeTruthy();
  expect(screen.getByText("검색 횟수: 사용 1 / 상한 1")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "이 재료로 초안 쓰기" }));
  expect(onUse).toHaveBeenCalledWith("run_1");
});

test("cacheHit, partial, budget-exhausted 안내를 표시한다", async () => {
  runResponse = { status: 200, data: { ...okRun, status: "partial", cacheHit: true } };
  render(<ResearchView />);
  fill(3);
  submit();
  expect(await screen.findByText("이전 결과를 재사용해 추가 비용이 없어요.")).toBeTruthy();
  expect(screen.getByText(/일부 링크만 수집했어요/)).toBeTruthy();

  runResponse = { status: 200, data: { ...okRun, status: "budget-exhausted" } };
  submit();
  expect(await screen.findByText(/지금까지 모은 결과만/)).toBeTruthy();
});

test("429 budget_exhausted 오류를 명확히 표시한다", async () => {
  runResponse = { status: 429, data: { error: { code: "budget_exhausted", message: "limit" } } };
  render(<ResearchView />);
  fill(3);
  submit();
  expect((await screen.findByRole("alert")).textContent).toMatch(/예산 한도에 도달/);
});

test("최근 실행 목록을 표시한다", async () => {
  render(<ResearchView />);
  expect(await screen.findByRole("list", { name: "최근 실행 목록" })).toBeTruthy();
  expect(screen.getByText("부분 결과")).toBeTruthy();
});
