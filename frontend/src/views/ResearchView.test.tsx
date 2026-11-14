import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ResearchView } from "./ResearchView";

type Body = Record<string, unknown>;
let calls: Body[];
let runResponse: { status: number; data: unknown };

const okRun = {
  runId: "run_1",
  status: "completed",
  keyword: "제주 여행",
  summary: {
    keyword: "제주 여행",
    overview: "제주 여행 개요 문장",
    sections: [{ heading: "준비물", points: [{ text: "우산을 챙기세요", sourceIds: ["src_1"] }] }],
    titleIdeas: ["제주 3박4일"],
    keywords: ["제주", "여행"],
  },
  sources: [
    { sourceId: "src_1", sourceType: "web-search", status: "succeeded", title: "A", domain: "a.example", url: "https://a.example/x" },
    { sourceId: "src_2", status: "failed", title: "B", errorMessage: "timeout" },
    { sourceId: "src_3", status: "skipped", title: "C" },
  ],
  usage: { requests: 3, inputChars: 100, outputTokens: 0, searchCalls: 1 },
  budget: { maxSearchCalls: 1 },
  cacheHit: false,
  controls: [] as string[],
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
        return {
          ok: true,
          status: 200,
          json: async () => ({
            runs: [
              { runId: "run_0", status: "partial", requestedAt: "2025-01-10T03:00:00.000Z", keyword: "서울 맛집", overview: "서울 맛집 한 줄 요약" },
              { runId: "run_old", status: "completed", requestedAt: "2025-01-09T03:00:00.000Z" },
            ],
          }),
        };
      }
      if (body.action === "get-research-run") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            run: { runId: body.runId, status: "partial", keyword: "서울 맛집", usage: {} },
            summary: { overview: "서울 상세 개요", sections: [{ heading: "거리", points: [{ text: "성수동", sourceIds: [] }] }] },
            sources: [],
          }),
        };
      }
      return { ok: runResponse.status < 400, status: runResponse.status, json: async () => runResponse.data };
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

const typeKeyword = (v = "제주 여행") => fireEvent.change(screen.getByLabelText("키워드"), { target: { value: v } });
const submit = () => fireEvent.click(screen.getByRole("button", { name: "자료 수집하기" }));
const runCalls = () => calls.filter((c) => c.action === "run-research");

test("안내문: 키워드가 주 입력이고 링크는 접힌 선택 영역이며 네이버 안내가 있다", () => {
  render(<ResearchView />);
  expect(screen.getByRole("heading", { name: "글 재료 수집" })).toBeTruthy();
  expect(screen.getByLabelText("키워드")).toBeTruthy();
  expect(screen.getByText(/참고 링크 추가 \(선택, 최대 5개\)/)).toBeTruthy();
  expect(screen.getByText(/네이버 블로그 링크는 수집할 수 없어요/)).toBeTruthy();
  expect(screen.queryByLabelText("웹 검색으로 보완하기")).toBeNull();
  expect(screen.queryByLabelText(/검색어/)).toBeNull();
  expect(screen.queryAllByLabelText(/참고 링크 \d/)).toHaveLength(0);
});

test("링크는 5개까지 추가/삭제할 수 있다", () => {
  render(<ResearchView />);
  const add = () => screen.getByRole("button", { name: "링크 추가" }) as HTMLButtonElement;
  for (let i = 0; i < 5; i++) fireEvent.click(add());
  expect(screen.getAllByLabelText(/참고 링크 \d/)).toHaveLength(5);
  expect(add().disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "링크 1 삭제" }));
  expect(screen.getAllByLabelText(/참고 링크 \d/)).toHaveLength(4);
});

test("키워드가 비면 요청 없이 alert, 잘못된 링크도 요청하지 않는다", async () => {
  render(<ResearchView />);
  submit();
  expect((await screen.findByRole("alert")).textContent).toMatch(/키워드를 입력/);
  typeKeyword();
  fireEvent.click(screen.getByRole("button", { name: "링크 추가" }));
  fireEvent.change(screen.getByLabelText("참고 링크 1"), { target: { value: "ftp://x.example" } });
  submit();
  expect((await screen.findByRole("alert")).textContent).toMatch(/1번째 링크/);
  expect(runCalls()).toHaveLength(0);
});

test("링크 0개로 keyword만 제출하고 search 필드는 보내지 않는다", async () => {
  render(<ResearchView />);
  typeKeyword("  제주 여행  ");
  submit();
  await screen.findByText(/실행 상태: 완료/);
  const req = runCalls()[0];
  expect(req.keyword).toBe("제주 여행");
  expect(req.links).toEqual([]);
  expect(req).not.toHaveProperty("search");
});

test("네이버 링크 400 오류 메시지를 표시한다", async () => {
  runResponse = {
    status: 400,
    data: { error: { code: "validation_error", message: "네이버 블로그 링크는 수집할 수 없어요." } },
  };
  render(<ResearchView />);
  typeKeyword();
  submit();
  expect((await screen.findByRole("alert")).textContent).toMatch(/네이버 블로그 링크는 수집할 수 없어요/);
});

test("결과: sections 렌더링, 출처 표, 사용량 접힘", async () => {
  render(<ResearchView />);
  typeKeyword();
  submit();
  await screen.findByText(/실행 상태: 완료/);
  expect(screen.getByText("제주 여행 개요 문장")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "준비물" })).toBeTruthy();
  expect(screen.getByText(/우산을 챙기세요/)).toBeTruthy();
  expect(screen.getByText("제주 3박4일")).toBeTruthy();
  expect(screen.getByText("여행")).toBeTruthy();
  const table = screen.getByRole("list", { name: "출처 목록" });
  expect(within(table).getAllByRole("listitem")).toHaveLength(3);
  expect(within(table).getByText("성공")).toBeTruthy();
  expect(within(table).getByText("실패")).toBeTruthy();
  expect(within(table).getByText("건너뜀")).toBeTruthy();
  expect(screen.getByText(/사유: timeout/)).toBeTruthy();
  expect(screen.getByText("검색 횟수: 사용 1 / 상한 1")).toBeTruthy();
});

test("초안 버튼은 결과 전에는 비활성, 후에는 같은 컨테이너에서 활성", async () => {
  const onUse = vi.fn();
  render(<ResearchView onUseInDraft={onUse} />);
  const card = screen.getByRole("region", { name: "수집 결과" });
  const before = within(card).getByRole("button", { name: "이 재료로 초안 쓰러 가기" }) as HTMLButtonElement;
  expect(before.disabled).toBe(true);
  const footer = before.parentElement;
  typeKeyword();
  submit();
  await screen.findByText(/실행 상태: 완료/);
  const after = within(card).getByRole("button", { name: "이 재료로 초안 쓰러 가기" }) as HTMLButtonElement;
  expect(after.parentElement).toBe(footer);
  expect(after.disabled).toBe(false);
  fireEvent.click(after);
  expect(onUse).toHaveBeenCalledWith("run_1");
});

test("cacheHit, partial, budget-exhausted, 검색 키 미설정 안내를 표시한다", async () => {
  runResponse = { status: 200, data: { ...okRun, status: "partial", cacheHit: true, controls: ["search_provider_unavailable"] } };
  render(<ResearchView />);
  typeKeyword();
  submit();
  expect(await screen.findByText("이전 결과를 재사용해 추가 비용이 없어요.")).toBeTruthy();
  expect(screen.getByText(/일부 출처만 수집했어요/)).toBeTruthy();
  expect(screen.getByText(/웹 검색 키가 아직 설정되지 않아/)).toBeTruthy();

  runResponse = { status: 200, data: { ...okRun, status: "budget-exhausted" } };
  submit();
  expect(await screen.findByText(/지금까지 모은 결과만/)).toBeTruthy();
});

test("429 budget_exhausted 오류를 명확히 표시한다", async () => {
  runResponse = { status: 429, data: { error: { code: "budget_exhausted", message: "limit" } } };
  render(<ResearchView />);
  typeKeyword();
  submit();
  expect((await screen.findByRole("alert")).textContent).toMatch(/예산 한도에 도달/);
});

test("최근 실행 행에 키워드·날짜·상태·overview를 표시하고 클릭하면 get-research-run으로 연다", async () => {
  render(<ResearchView />);
  const list = await screen.findByRole("list", { name: "최근 실행 목록" });
  expect(within(list).getByText("서울 맛집")).toBeTruthy();
  expect(within(list).getByText("2025-01-10 12:00 KST")).toBeTruthy();
  expect(within(list).getByText("부분 결과")).toBeTruthy();
  expect(within(list).getByText("서울 맛집 한 줄 요약")).toBeTruthy();
  expect(within(list).getByText("키워드 없음(이전 형식)")).toBeTruthy();

  fireEvent.click(within(list).getByText("서울 맛집").closest("button")!);
  expect(await screen.findByText("서울 상세 개요")).toBeTruthy();
  const get = calls.find((c) => c.action === "get-research-run")!;
  expect(get.runId).toBe("run_0");
  expect(screen.getByRole("heading", { name: "거리" })).toBeTruthy();
});

test("수집 중 로딩 표시는 결과 카드 안에서만 바뀐다", async () => {
  let release: () => void = () => {};
  const gate = new Promise<void>((r) => (release = r));
  const base = (globalThis.fetch as unknown as (u: string, i: { body: string }) => Promise<unknown>);
  vi.stubGlobal("fetch", vi.fn(async (u: string, init: { body: string }) => {
    if ((JSON.parse(init.body) as Body).action === "run-research") await gate;
    return base(u, init);
  }));
  render(<ResearchView onUseInDraft={() => {}} />);
  typeKeyword();
  submit();
  const card = screen.getByRole("region", { name: "수집 결과" });
  expect(within(card).getByText(/수집하는 중|모으는 중/)).toBeTruthy();
  expect((within(card).getByRole("button", { name: "이 재료로 초안 쓰러 가기" }) as HTMLButtonElement).disabled).toBe(true);
  release();
  await waitFor(() => expect(within(card).getByText(/실행 상태: 완료/)).toBeTruthy());
});
