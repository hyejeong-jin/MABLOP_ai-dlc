// 글 재료 수집: 정보 수집 링크 3~5개 + 선택적 웹 검색 보완. Browser -> Lambda only.
// 이 링크는 말투 학습(Style_Learner)과 무관하다.
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { CircleAlert } from "lucide-react";
import {
  ApiRequestError,
  getResearchRun,
  listResearchRuns,
  runResearch,
  type ResearchBudget,
  type ResearchRunRequest,
  type ResearchRunResult,
  type ResearchRunSource,
  type ResearchRunSummary,
  type ResearchSummaryData,
  type ResearchUsage,
} from "../api";
import { ResearchSummary } from "../components/ResearchSummary";
import { GrantSelect } from "../grants/GrantSelect";
import { useGrants } from "../grants/useGrants";
import { formatKstDate } from "../format";

export const MIN_LINKS = 3;
export const MAX_LINKS = 5;
export const MAX_QUERIES = 2;

const STATUS_TEXT: Record<string, string> = {
  completed: "완료",
  partial: "부분 결과",
  "budget-exhausted": "예산 소진",
  failed: "실패",
};
const SOURCE_STATUS_TEXT: Record<string, string> = {
  succeeded: "성공",
  failed: "실패",
  skipped: "건너뜀",
};
const CONTROL_TEXT: Record<string, string> = {
  max_search_calls_reached: "검색 횟수 상한에 도달해 일부 검색어는 실행하지 않았어요.",
  search_provider_unavailable: "웹 검색을 사용할 수 없어 검색 보완은 건너뛰었어요.",
  time_guard_reached: "시간 제한에 도달해 수집을 일찍 멈췄어요.",
};

export function isHttpUrl(raw: string): boolean {
  try {
    const url = new URL(raw.trim());
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

/** Client-side check mirroring the server contract. Returns a message or null. */
export function validateLinks(links: string[]): string | null {
  const filled = links.map((l) => l.trim()).filter(Boolean);
  if (filled.length < MIN_LINKS) return `링크를 ${MIN_LINKS}개 이상 입력해 주세요. (현재 ${filled.length}개)`;
  if (filled.length > MAX_LINKS) return `링크는 최대 ${MAX_LINKS}개까지 입력할 수 있어요.`;
  if (filled.length !== links.length) return "비어 있는 입력칸이 있어요. 링크를 채우거나 행을 삭제해 주세요.";
  const bad = links.findIndex((l) => !isHttpUrl(l));
  if (bad >= 0) return `${bad + 1}번째 링크는 http:// 또는 https:// 로 시작하는 주소여야 해요.`;
  return null;
}

const errMsg = (e: unknown): string => {
  if (e instanceof ApiRequestError) {
    if (e.code === "budget_exhausted") return `예산 한도에 도달해 수집을 멈췄어요. ${e.message}`;
    if (e.code === "validation_error") return `입력을 확인해 주세요. ${e.message}`;
    return e.message;
  }
  return "요청을 처리하지 못했습니다.";
};

function UsageBudget({ usage, budget, cacheHit }: { usage: ResearchUsage; budget: ResearchBudget; cacheHit: boolean }) {
  const cap = (v: number | null | undefined) => (typeof v === "number" ? `${v}` : "제한 없음");
  const rows: Array<[string, number | undefined, number | null | undefined]> = [
    ["요청 수", usage.requests, budget.maxRequests],
    ["입력 글자", usage.inputChars, budget.maxInputChars],
    ["출력 토큰", usage.outputTokens, budget.maxOutputTokens],
    ["검색 횟수", usage.searchCalls, budget.maxSearchCalls],
  ];
  return (
    <section className="feature-card" aria-label="사용량과 예산">
      <h3 className="feature-card-title">사용량과 예산</h3>
      {cacheHit && <p role="status">이전 결과를 재사용해 추가 비용이 없어요.</p>}
      <ul className="research-source-list">
        {rows.map(([label, used, max]) => (
          <li key={label} className="source-meta">
            {label}: 사용 {used ?? 0} / 상한 {cap(max)}
          </li>
        ))}
      </ul>
    </section>
  );
}

function SourceList({ sources }: { sources: ResearchRunSource[] }) {
  if (sources.length === 0) return null;
  return (
    <section className="feature-card" aria-label="수집한 출처">
      <h3 className="feature-card-title">수집한 출처</h3>
      <ul className="research-source-list">
        {sources.map((s) => {
          const status = s.status ?? "";
          return (
            <li key={s.sourceId} className="research-source">
              <div className="research-source-header">
                <span className="research-source-title">{s.title || s.domain || s.url || s.normalizedUrl || "출처"}</span>
                <span>{SOURCE_STATUS_TEXT[status] ?? status}</span>
              </div>
              {status !== "succeeded" && s.errorMessage ? (
                <p className="source-meta">사유: {s.errorMessage}</p>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

interface ResearchViewProps {
  /** 이후 초안 작성에 연결할 때 사용 (8.2/8.3). */
  onUseInDraft?: (runId: string) => void;
}

export function ResearchView({ onUseInDraft }: ResearchViewProps = {}) {
  const grants = useGrants();
  const [links, setLinks] = useState<string[]>(["", "", ""]);
  const [searchOn, setSearchOn] = useState(false);
  const [queries, setQueries] = useState<string[]>([""]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ResearchRunResult | null>(null);
  const [recent, setRecent] = useState<ResearchRunSummary[]>([]);
  const [recentError, setRecentError] = useState<string | null>(null);
  const [loadingRunId, setLoadingRunId] = useState<string | null>(null);

  const loadRecent = useCallback(async () => {
    setRecentError(null);
    try {
      const res = await listResearchRuns({ limit: 5 });
      setRecent(res.runs);
    } catch (e) {
      setRecentError(errMsg(e));
    }
  }, []);

  useEffect(() => {
    void loadRecent();
  }, [loadRecent]);

  const setLink = (i: number, v: string) => setLinks((p) => p.map((x, j) => (j === i ? v : x)));
  const setQuery = (i: number, v: string) => setQueries((p) => p.map((x, j) => (j === i ? v : x)));

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (running) return;
    const linkError = validateLinks(links);
    if (linkError) return setError(linkError);
    const cleanQueries = queries.map((q) => q.trim()).filter(Boolean).slice(0, MAX_QUERIES);
    if (searchOn && cleanQueries.length === 0) return setError("웹 검색 보완을 켰다면 검색어를 1개 이상 입력해 주세요.");

    const req: ResearchRunRequest = { links: links.map((l) => l.trim()) };
    if (searchOn) {
      req.search = { enabled: true, queries: cleanQueries };
      req.budget = { maxSearchCalls: cleanQueries.length };
    }
    setError(null);
    setRunning(true);
    try {
      setResult(await runResearch(grants.withGrant({ ...req }) as unknown as ResearchRunRequest & { grantId?: string }));
      void loadRecent();
    } catch (err) {
      grants.handleGrantError(err);
      setError(errMsg(err));
    } finally {
      setRunning(false);
    }
  }

  async function openRun(runId: string) {
    setLoadingRunId(runId);
    setError(null);
    try {
      const res = await getResearchRun(runId);
      setResult({
        runId,
        status: res.run.status,
        summary: res.summary as ResearchSummaryData,
        sources: res.sources,
        usage: res.run.usage ?? {},
        budget: {},
        cacheHit: false,
        controls: [],
      });
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setLoadingRunId(null);
    }
  }

  const status = result?.status ?? "";
  const controlNotes = (result?.controls ?? []).map((c) => CONTROL_TEXT[c]).filter(Boolean);

  return (
    <div className="feature-grid">
      <header>
        <h2>글 재료 수집</h2>
        <p id="research-guide" className="source-meta">
          정보를 모을 링크를 3~5개 붙여 넣으면, 사실 근거를 모아 요약해요. 이 링크는 말투 학습에 쓰이지 않아요.
        </p>
      </header>

      <form className="feature-card" onSubmit={submit} noValidate aria-describedby="research-guide">
        <GrantSelect grants={grants} />
        <h3 className="feature-card-title">정보 수집 링크</h3>
        <div className="link-input-list">
          {links.map((value, i) => (
            <div className="link-input-row" key={i}>
              <div>
                <label htmlFor={`research-link-${i}`}>정보 수집 링크 {i + 1}</label>
                <input
                  id={`research-link-${i}`}
                  type="url"
                  inputMode="url"
                  placeholder="https://"
                  value={value}
                  onChange={(e) => setLink(i, e.target.value)}
                  disabled={running}
                />
              </div>
              <button
                type="button"
                onClick={() => setLinks((p) => p.filter((_, j) => j !== i))}
                disabled={running || links.length <= MIN_LINKS}
                aria-label={`링크 ${i + 1} 삭제`}
              >
                삭제
              </button>
            </div>
          ))}
        </div>
        <button
          type="button"
          onClick={() => setLinks((p) => [...p, ""])}
          disabled={running || links.length >= MAX_LINKS}
        >
          링크 추가
        </button>
        <p className="source-meta">{links.length}/{MAX_LINKS}개 (최소 {MIN_LINKS}개)</p>

        <div>
          <label htmlFor="research-search-on">
            <input
              id="research-search-on"
              type="checkbox"
              checked={searchOn}
              onChange={(e) => setSearchOn(e.target.checked)}
              disabled={running}
              aria-describedby="research-search-cost"
            />{" "}
            웹 검색으로 보완하기
          </label>
          <p id="research-search-cost" className="source-meta">
            기본은 꺼져 있어요. 켜면 검색 1회당 추가 비용이 발생해요.
          </p>
        </div>
        {searchOn && (
          <div className="link-input-list">
            {queries.map((q, i) => (
              <div className="link-input-row" key={i}>
                <div>
                  <label htmlFor={`research-query-${i}`}>검색어 {i + 1}</label>
                  <input
                    id={`research-query-${i}`}
                    type="text"
                    value={q}
                    onChange={(e) => setQuery(i, e.target.value)}
                    disabled={running}
                  />
                </div>
                <button
                  type="button"
                  onClick={() => setQueries((p) => p.filter((_, j) => j !== i))}
                  disabled={running || queries.length <= 1}
                  aria-label={`검색어 ${i + 1} 삭제`}
                >
                  삭제
                </button>
              </div>
            ))}
            <button
              type="button"
              onClick={() => setQueries((p) => [...p, ""])}
              disabled={running || queries.length >= MAX_QUERIES}
            >
              검색어 추가
            </button>
            <p className="source-meta">검색어는 최대 {MAX_QUERIES}개, 검색 횟수 상한은 검색어 수와 같아요.</p>
          </div>
        )}

        {error && (
          <p role="alert" className="error">
            <CircleAlert size={14} aria-hidden="true" /> {error}
          </p>
        )}
        <button type="submit" disabled={running || !grants.canCallBedrock}>
          {running ? "수집 중..." : "자료 수집하기"}
        </button>
        {running && <p role="status">링크에서 근거를 모으는 중이에요. 잠시만 기다려 주세요.</p>}
      </form>

      {result && (
        <section aria-label="수집 결과" className="feature-grid">
          <p role="status">
            실행 상태: {STATUS_TEXT[status] ?? status}
          </p>
          {status === "partial" && (
            <p role="status">일부 링크만 수집했어요. 성공한 출처의 내용만 요약에 반영돼요.</p>
          )}
          {status === "budget-exhausted" && (
            <p role="alert">예산 한도에 도달해 수집을 멈췄어요. 지금까지 모은 결과만 보여드려요.</p>
          )}
          {status === "failed" && <p role="alert">수집에 실패했어요. 링크를 확인하고 다시 시도해 주세요.</p>}
          {controlNotes.map((n) => (
            <p key={n} role="status" className="source-meta">{n}</p>
          ))}
          <UsageBudget usage={result.usage ?? {}} budget={result.budget ?? {}} cacheHit={result.cacheHit} />
          <ResearchSummary summary={result.summary} sources={result.sources} />
          <SourceList sources={result.sources} />
          {onUseInDraft && status !== "failed" && (
            <button type="button" onClick={() => onUseInDraft(result.runId)}>
              이 재료로 초안 쓰기
            </button>
          )}
        </section>
      )}

      <section className="feature-card" aria-label="최근 실행">
        <h3 className="feature-card-title">최근 실행</h3>
        {recentError && <p role="alert" className="error">{recentError}</p>}
        {recent.length === 0 && !recentError ? (
          <p className="source-meta">아직 실행 기록이 없어요.</p>
        ) : (
          <ul className="research-source-list" aria-label="최근 실행 목록">
            {recent.map((r) => (
              <li key={r.runId} className="research-source">
                <div className="research-source-header">
                  <span>{STATUS_TEXT[r.status] ?? r.status}</span>
                  <span className="source-meta">{r.requestedAt ? formatKstDate(r.requestedAt) : ""}</span>
                </div>
                <button type="button" onClick={() => void openRun(r.runId)} disabled={loadingRunId === r.runId}>
                  결과 보기
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
