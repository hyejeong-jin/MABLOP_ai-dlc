import { useEffect, useState } from "react";
import { ApiRequestError } from "../api";
import { postJson } from "../api";
import { formatKst } from "../format";

interface UsageModel { modelId: string; label: string; calls: number; inputTokens: number; outputTokens: number; usd: number; priced: boolean; }
interface UsageResponse { month: string; totalUsd: number; totalTokens?: number; resetAt: string; models: UsageModel[]; activity?: Record<string, number>; pricing: { label: string; inputPerMillion: number; outputPerMillion: number }[]; }
const FALLBACK = "사용량을 불러오지 못했습니다. 잠시 후 다시 시도하세요.";
const usd = (v: number, d = 4) => `$${v.toFixed(d)}`;

function heatDays(activity: Record<string, number> | undefined): { date: string; tokens: number }[] {
  const out: { date: string; tokens: number }[] = [];
  const end = new Date();
  for (let i = 181; i >= 0; i -= 1) {
    const d = new Date(end); d.setHours(12, 0, 0, 0); d.setDate(end.getDate() - i);
    const date = d.toISOString().slice(0, 10);
    out.push({ date, tokens: activity?.[date] ?? 0 });
  }
  return out;
}

export function UsageSection() {
  const [data, setData] = useState<UsageResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { let alive = true; postJson<UsageResponse>({ action: "get-usage" }).then((r) => alive && setData(r)).catch((e) => alive && setError(e instanceof ApiRequestError && e.message ? e.message : FALLBACK)); return () => { alive = false; }; }, []);
  const days = heatDays(data?.activity);
  const max = Math.max(1, ...days.map((d) => d.tokens));
  const totalTokens = data?.totalTokens ?? data?.models?.reduce((n, m) => n + m.inputTokens + m.outputTokens, 0) ?? 0;
  return <section className="card usage-card" aria-labelledby="profile-usage-title">
    <div className="usage-header"><div><h3 className="section-title" id="profile-usage-title">내 월 사용량</h3><p className="meta">AWS Bedrock 사용 비용과 모델별 상세 내역</p></div><span className="usage-period">{data?.month ?? "-"}</span></div>
    {error && <p className="callout callout-error" role="alert">{error}</p>}
    {!data && !error && <p className="meta">불러오는 중...</p>}
    {data && <>
      <div className="usage-summary-grid"><div><span>이번 달 예상 비용</span><strong>{usd(data.totalUsd ?? 0)}</strong></div><div><span>누적 토큰 사용량</span><strong>{totalTokens.toLocaleString("ko-KR")}</strong></div><div><span>다음 초기화</span><strong>{formatKst(data.resetAt)}</strong></div></div>
      <h4 className="usage-subtitle">모델별 비용</h4>
      <div className="usage-table-wrap"><table className="usage-table"><thead><tr><th>서비스 / 모델</th><th>호출</th><th>입력 토큰</th><th>출력 토큰</th><th>예상 비용</th></tr></thead><tbody>{(data.models ?? []).length === 0 ? <tr><td colSpan={5} className="table-empty">이번 달에는 아직 사용 기록이 없습니다.</td></tr> : data.models.map((m) => <tr key={m.modelId}><td><strong>{m.label}</strong><small>{m.modelId}</small></td><td>{m.calls.toLocaleString("ko-KR")}</td><td>{m.inputTokens.toLocaleString("ko-KR")}</td><td>{m.outputTokens.toLocaleString("ko-KR")}</td><td>{m.priced ? usd(m.usd, 6) : "단가 없음"}</td></tr>)}</tbody></table></div>
      <p className="meta usage-note">AWS 공개 단가와 반환 토큰으로 계산한 예상치입니다. 실제 청구서와 다를 수 있습니다.</p>
      <h4 className="usage-subtitle activity-title">포스트 작성 활동 <span>최근 6개월</span></h4>
      <div className="activity-grid" aria-label="최근 포스트 작성 활동"><div className="activity-months"><span>6개월 전</span><span>오늘</span></div><div className="heatmap">{days.map((d) => <span key={d.date} title={`${d.date}: ${d.tokens.toLocaleString("ko-KR")} 토큰`} className={`heat-cell level-${d.tokens === 0 ? 0 : Math.min(4, Math.ceil((d.tokens / max) * 4))}`} />)}</div><div className="heat-legend"><span>적음</span><i className="heat-cell level-0" /><i className="heat-cell level-1" /><i className="heat-cell level-2" /><i className="heat-cell level-3" /><i className="heat-cell level-4" /><span>많음</span></div></div>
    </>}
  </section>;
}