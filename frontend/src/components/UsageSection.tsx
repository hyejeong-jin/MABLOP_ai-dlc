// "내 월 사용량" and "포스트 작성 활동" on the 마이페이지: Bedrock cost per model, cumulative
// tokens, and a six-month writing-activity heat map (drafts written + blog posts learned).
import { useEffect, useState } from "react";

import { ApiRequestError, postJson } from "../api";
import { formatKst } from "../format";

interface UsageModel {
  modelId: string;
  label: string;
  calls: number;
  inputTokens: number;
  outputTokens: number;
  usd: number;
  priced: boolean;
}

interface UsageResponse {
  month: string;
  totalUsd: number;
  totalTokens?: number;
  resetAt: string;
  models: UsageModel[];
  activity?: Record<string, number>;
  pricing: { label: string; inputPerMillion: number; outputPerMillion: number }[];
}

const FALLBACK = "사용량을 불러오지 못했습니다. 잠시 후 다시 시도하세요.";
const HEAT_DAYS = 182; // about six months
const DAY_MS = 24 * 60 * 60 * 1000;
const KST_DATE = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const usd = (value: number, digits = 4) => `$${value.toFixed(digits)}`;
const count = (value: number) => value.toLocaleString("ko-KR");

export interface HeatCell {
  date: string;
  count: number;
}

/** Cells for the last HEAT_DAYS days in Korea time, padded so each column starts on a Sunday. */
export function heatCells(activity: Record<string, number> | undefined, now: Date = new Date()): (HeatCell | null)[] {
  const today = KST_DATE.format(now);
  const base = new Date(`${today}T12:00:00+09:00`).getTime();
  const cells: HeatCell[] = [];
  for (let i = HEAT_DAYS - 1; i >= 0; i -= 1) {
    const date = KST_DATE.format(new Date(base - i * DAY_MS));
    cells.push({ date, count: activity?.[date] ?? 0 });
  }
  // 12:00 KST is 03:00 UTC on the same date, so the UTC weekday is the Korea weekday.
  const lead = new Date(`${cells[0].date}T12:00:00+09:00`).getUTCDay();
  return [...Array<null>(lead).fill(null), ...cells];
}

export function heatLevel(n: number): 0 | 1 | 2 | 3 | 4 {
  if (n <= 0) return 0;
  if (n === 1) return 1;
  if (n === 2) return 2;
  if (n <= 4) return 3;
  return 4;
}

export function UsageSection() {
  const [data, setData] = useState<UsageResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    postJson<UsageResponse>({ action: "get-usage" })
      .then((res) => alive && setData(res))
      .catch((err) => alive && setError(err instanceof ApiRequestError && err.message ? err.message : FALLBACK));
    return () => {
      alive = false;
    };
  }, []);

  const cells = heatCells(data?.activity);
  const columns = Math.ceil(cells.length / 7);
  const activeDays = cells.filter((c) => c && c.count > 0).length;
  const totalActions = cells.reduce((n, c) => n + (c?.count ?? 0), 0);
  const totalTokens =
    data?.totalTokens ?? data?.models?.reduce((n, m) => n + m.inputTokens + m.outputTokens, 0) ?? 0;
  const models = data?.models ?? [];

  return (
    <>
      <section className="card usage-card" aria-labelledby="profile-usage-title">
        <div className="usage-header">
          <div>
            <h3 className="section-title" id="profile-usage-title">
              내 월 사용량
            </h3>
            <p className="meta">AWS Bedrock 사용 비용과 모델별 상세 내역</p>
          </div>
          <span className="usage-period">{data?.month ?? "-"}</span>
        </div>
        {error && (
          <p className="callout callout-error" role="alert">
            {error}
          </p>
        )}
        {!data && !error && <p className="meta">불러오는 중...</p>}
        {data && (
          <>
            <div className="usage-summary-grid">
              <div>
                <span>이번 달 예상 비용</span>
                <strong>{usd(data.totalUsd ?? 0)}</strong>
              </div>
              <div>
                <span>누적 토큰 사용량 (최근 6개월)</span>
                <strong>{count(totalTokens)}</strong>
              </div>
              <div>
                <span>다음 초기화</span>
                <strong>{formatKst(data.resetAt)}</strong>
              </div>
            </div>
            <h4 className="usage-subtitle">모델별 비용</h4>
            <div className="usage-table-wrap">
              <table className="usage-table">
                <thead>
                  <tr>
                    <th>서비스 / 모델</th>
                    <th>호출</th>
                    <th>입력 토큰</th>
                    <th>출력 토큰</th>
                    <th>예상 비용</th>
                  </tr>
                </thead>
                <tbody>
                  {models.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="table-empty">
                        이번 달에는 아직 사용 기록이 없습니다.
                      </td>
                    </tr>
                  ) : (
                    models.map((m) => (
                      <tr key={m.modelId}>
                        <td>
                          <strong>{m.label}</strong>
                          <small>{m.modelId}</small>
                        </td>
                        <td>{count(m.calls)}</td>
                        <td>{count(m.inputTokens)}</td>
                        <td>{count(m.outputTokens)}</td>
                        <td>{m.priced ? usd(m.usd, 6) : "단가 없음"}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
            <p className="meta usage-note">
              AWS 공개 단가와 반환 토큰으로 계산한 예상치입니다. 실제 청구서와 다를 수 있습니다.
            </p>
          </>
        )}
      </section>

      <section className="card" aria-labelledby="profile-activity-title">
        <div className="usage-header">
          <div>
            <h3 className="section-title" id="profile-activity-title">
              포스트 작성 활동
            </h3>
            <p className="meta">초안을 쓰거나 블로그 글을 학습한 날이 진하게 표시돼요 (한국 시간)</p>
          </div>
          <span className="usage-period">최근 6개월</span>
        </div>
        {data && (
          <p className="activity-summary">
            <strong>{count(activeDays)}</strong>일 활동 · 초안·학습 <strong>{count(totalActions)}</strong>회
          </p>
        )}
        <div className="activity-grid" role="img" aria-label="최근 6개월 포스트 작성 활동">
          <div className="heatmap" style={{ ["--cols" as string]: columns }}>
            {cells.map((cell, i) =>
              cell === null ? (
                <span key={`pad-${i}`} className="heat-cell heat-pad" />
              ) : (
                <span
                  key={cell.date}
                  title={`${cell.date}: ${cell.count}회`}
                  className={`heat-cell level-${heatLevel(cell.count)}`}
                />
              ),
            )}
          </div>
          <div className="heat-legend">
            <span>적음</span>
            {[0, 1, 2, 3, 4].map((n) => (
              <i key={n} className={`heat-cell level-${n}`} />
            ))}
            <span>많음</span>
          </div>
        </div>
      </section>
    </>
  );
}