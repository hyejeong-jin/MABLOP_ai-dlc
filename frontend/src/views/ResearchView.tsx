// 글 재료 수집: 키워드 하나로 웹 검색, 참고 링크(선택, 최대 5개)로 보완. Browser -> Lambda only.
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
import { ResearchSummary, safeExternalUrl } from "../components/ResearchSummary";
import { GrantSelect } from "../grants/GrantSelect";
import { useGrants } from "../grants/useGrants";
import { formatKstDateTime } from "../format";

export const MAX_LINKS = 5;
export const MAX_KEYWORD = 80;

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
  ai_overview_unavailable: "이 검색어에는 구글 AI 개요가 표시되지 않았어요.",
  serp_provider_unavailable: "구글 AI 개요 키가 아직 설정되지 않아 가져오지 못했어요. 운영자에게 문의해 주세요.",
  serp_request_failed: "구글 AI 개요를 가져오는 중 오류가 났어요. 잠시 후 다시 시도해 주세요.",
  max_search_calls_reached: "검색 횟수 상한에 도달해 일부 검색은 실행하지 않았어요.",
  search_provider_unavailable: "웹 검색 키가 아직 설정되지 않아 검색 결과를 가져오지 못했어요. 운영자에게 문의해 주세요.",
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

/** Client-side check mirroring the server contract (links are optional, 0~5). Returns a message or null. */
export function validateLinks(links: string[]): string | null {
  if (links.length > MAX_LINKS) return `링크는 최대 ${MAX_LINKS}개까지 입력할 수 있어요.`;
  const empty = links.findIndex((l) => !l.trim());
  if (empty >= 0) return "비어 있는 입력칸이 있어요. 링크를 채우거나 행을 삭제해 주세요.";
  const bad = links.findIndex((l) => !isHttpUrl(l));
  if (bad >= 0) return `${bad + 1}번째 링크는 http:// 또는 https:// 로 시작하는 주소여야 해요.`;
  return null;
}

export function validateKeyword(keyword: string): string | null {
  const k = keyword.trim();
  if (!k) return "키워드를 입력해 주세요.";
  if (k.length > MAX_KEYWORD) return `키워드는 ${MAX_KEYWORD}자 이하로 입력해 주세요.`;
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

function UsageBudget({ usage, budget }: { usage: ResearchUsage; budget: ResearchBudget }) {
  const cap = (v: number | null | undefined) => (typeof v === "number" ? `${v}` : "제한 없음");
  const rows: Array<[string, number | undefined, number | null | undefined]> = [
    ["요청 수", usage.requests, budget.maxRequests],
    ["입력 글자", usage.inputChars, budget.maxInputChars],
    ["출력 토큰", usage.outputTokens, budget.maxOutputTokens],
    ["검색 횟수", usage.searchCalls, budget.maxSearchCalls],
  ];
  return (
    <details className="research-usage">
      <summary>사용량과 예산</summary>
      <ul className="research-usage-list">
        {rows.map(([label, used, max]) => (
          <li key={label} className="source-meta">
            {label}: 사용 {used ?? 0} / 상한 {cap(max)}
          </li>
        ))}
      </ul>
    </details>
  );
}

type OverviewSource = ResearchRunSource & { excerpt?: string };

function AiOverviewCard({ sources, controls }: { sources: ResearchRunSource[]; controls: string[] }) {
  const [copied, setCopied] = useState(false);
  const overview = (sources as OverviewSource[]).find((s) => s.sourceType === "google-ai-overview" && s.status === "succeeded");
  const missing = !overview && controls.includes("ai_overview_unavailable");
  if (!overview && !missing) return null;
  async function copy(text: string) {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  }
  return (
    <section className="research-card" aria-label="구글 AI 개요">
      <h3 className="research-card-title">구글 AI 개요 (자동 수집)</h3>
      {overview ? (
        <>
          <p className="source-meta">복사해서 초안 작성 화면의 &apos;붙여넣을 재료&apos;에 넣으면 초안 재료로 쓸 수 있어요.</p>
          <div className="ai-overview-text" tabIndex={0}>{overview.excerpt}</div>
          <button type="button" className="btn-sm" onClick={() => copy(overview.excerpt ?? "")}>{copied ? "복사됨" : "복사"}</button>
        </>
      ) : (
        <p className="field-hint">이 검색어에는 구글 AI 개요가 표시되지 않았어요.</p>
      )}
    </section>
  );
}

function SourceTable({ sources }: { sources: ResearchRunSource[] }) {
  return (
    <section className="research-card" aria-label="수집한 출처">
      <h3 className="research-card-title">수집한 출처</h3>
      <ul className="research-table" aria-label="출처 목록">
        {sources.map((s, i) => {
          const status = s.status ?? "";
          const href = safeExternalUrl(s.url ?? s.normalizedUrl);
          const name = s.title || s.domain || s.url || s.normalizedUrl || "출처";
          return (
            <li key={s.sourceId} className="research-table-row">
              <span className="research-table-num">[{i + 1}]</span>
              <span className="research-table-title">
                {href ? (
                  <a href={href} target="_blank" rel="noopener noreferrer">{name}</a>
                ) : (
                  name
                )}
              </span>
              <span className="research-table-domain">{s.domain ?? ""}</span>
              <span className="research-table-status">
                <span>{SOURCE_STATUS_TEXT[status] ?? status}</span>
                {status !== "succeeded" && s.errorMessage ? (
                  <span className="source-meta">사유: {s.errorMessage}</span>
                ) : null}
              </span>
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
  const [keyword, setKeyword] = useState("");
  const [links, setLinks] = useState<string[]>([]);
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
      setRecent(Array.isArray(res?.runs) ? res.runs : []);
    } catch (e) {
      setRecentError(errMsg(e));
    }
  }, []);

  useEffect(() => {
    void loadRecent();
  }, [loadRecent]);

  const setLink = (i: number, v: string) => setLinks((p) => p.map((x, j) => (j === i ? v : x)));

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (running) return;
    const keywordError = validateKeyword(keyword);
    if (keywordError) return setError(keywordError);
    const linkError = validateLinks(links);
    if (linkError) return setError(linkError);

    const req: ResearchRunRequest = { keyword: keyword.trim(), links: links.map((l) => l.trim()) };
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
      const summary = res.summary as ResearchSummaryData;
      setResult({
        runId,
        status: res.run.status,
        summary,
        sources: res.sources,
        usage: res.run.usage ?? {},
        budget: {},
        cacheHit: false,
        controls: [],
        keyword: res.run.keyword ?? summary?.keyword,
      });
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setLoadingRunId(null);
    }
  }

  const status = result?.status ?? "";
  const controlNotes = (result?.controls ?? []).map((c) => CONTROL_TEXT[c]).filter(Boolean);
  const canUse = !!result && !running && status !== "failed" && !!onUseInDraft;

  return (
    <div className="research-stack">
      <header>
        <h2>글 재료 수집</h2>
        <p id="research-guide" className="source-meta">
          키워드를 웹에서 검색해 나온 결과를 정리해 드려요. 이 내용은 말투 학습에 쓰이지 않아요.
        </p>
      </header>

      <form className="research-card" onSubmit={submit} noValidate aria-describedby="research-guide">
        <div className="research-input-row">
          <GrantSelect grants={grants} />
          <div className="research-keyword">
            <label htmlFor="research-keyword">키워드</label>
            <input
              id="research-keyword"
              type="text"
              maxLength={MAX_KEYWORD}
              placeholder="예: 제주 여행 준비물"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              disabled={running}
            />
          </div>
          <button type="submit" className="btn-primary" disabled={running || !grants.canCallBedrock}>
            {running ? "수집 중..." : "자료 수집하기"}
          </button>
        </div>

        <details className="research-links">
          <summary>참고 링크 추가 (선택, 최대 {MAX_LINKS}개)</summary>
          <p className="source-meta">네이버 블로그 링크는 수집할 수 없어요. 다른 사이트 주소를 붙여 넣어 주세요.</p>
          <div className="link-input-list">
            {links.map((value, i) => (
              <div className="link-input-row" key={i}>
                <div>
                  <label htmlFor={`research-link-${i}`}>참고 링크 {i + 1}</label>
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
                  disabled={running}
                  aria-label={`링크 ${i + 1} 삭제`}
                >
                  삭제
                </button>
              </div>
            ))}
          </div>
          <button
            type="button"
            className="btn-sm"
            onClick={() => setLinks((p) => [...p, ""])}
            disabled={running || links.length >= MAX_LINKS}
          >
            링크 추가
          </button>
          <p className="source-meta">{links.length}/{MAX_LINKS}개</p>
        </details>

        {error && (
          <p role="alert" className="error">
            <CircleAlert size={14} aria-hidden="true" /> {error}
          </p>
        )}
      </form>

      <section className="research-card research-result" aria-label="수집 결과">
        <div className="research-status-line" role="status">
          {running ? (
            <span>수집 중...</span>
          ) : result ? (
            <>
              <span>실행 상태: {STATUS_TEXT[status] ?? status}</span>
              {result.keyword ? <span className="source-meta">키워드: {result.keyword}</span> : null}
              {result.cacheHit && <span>이전 결과를 재사용해 추가 비용이 없어요.</span>}
            </>
          ) : (
            <span className="source-meta">아직 수집한 결과가 없어요.</span>
          )}
        </div>
        <div className="research-result-body">
          {running ? (
            <p>키워드를 검색해 근거를 모으는 중이에요. 잠시만 기다려 주세요.</p>
          ) : result ? (
            <>
              {status === "partial" && <p role="status">일부 출처만 수집했어요. 성공한 출처의 내용만 요약에 반영돼요.</p>}
              {status === "budget-exhausted" && (
                <p role="alert">예산 한도에 도달해 수집을 멈췄어요. 지금까지 모은 결과만 보여드려요.</p>
              )}
              {status === "failed" && <p role="alert">수집에 실패했어요. 키워드나 링크를 확인하고 다시 시도해 주세요.</p>}
              {controlNotes.map((n) => (
                <p key={n} role="status" className="source-meta">{n}</p>
              ))}
              <UsageBudget usage={result.usage ?? {}} budget={result.budget ?? {}} />
              <ResearchSummary summary={result.summary} sources={result.sources} />
              <AiOverviewCard sources={result.sources} controls={result.controls ?? []} />
            </>
          ) : (
            <p className="source-meta">키워드를 입력하고 “자료 수집하기”를 누르면 여기에 정리돼요.</p>
          )}
        </div>
        <div className="research-card-footer">
          <button
            type="button"
            className="btn-primary"
            disabled={!canUse}
            onClick={() => result && onUseInDraft?.(result.runId)}
          >
            이 재료로 초안 쓰러 가기
          </button>
        </div>
      </section>

      {result && !running && result.sources.length > 0 && <SourceTable sources={result.sources} />}

      <section className="research-card" aria-label="최근 실행">
        <h3 className="research-card-title">최근 실행</h3>
        {recentError && <p role="alert" className="error">{recentError}</p>}
        {recent.length === 0 && !recentError ? (
          <p className="source-meta">아직 실행 기록이 없어요.</p>
        ) : (
          <ul className="research-recent-list" aria-label="최근 실행 목록">
            {recent.map((r) => {
              const when = formatKstDateTime(r.completedAt ?? r.requestedAt);
              return (
                <li key={r.runId}>
                  <button
                    type="button"
                    className="research-recent-row"
                    onClick={() => void openRun(r.runId)}
                    disabled={loadingRunId === r.runId}
                  >
                    <strong className="research-recent-keyword">{r.keyword || "키워드 없음(이전 형식)"}</strong>
                    <span className="source-meta">{when ? `${when} KST` : ""}</span>
                    <span className="source-meta">{STATUS_TEXT[r.status] ?? r.status}</span>
                    <span className="research-recent-overview">{r.overview ?? ""}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}