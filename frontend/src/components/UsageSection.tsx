// "내 월 사용량": this month's Bedrock usage of the signed-in user, per model, with an estimated cost.
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
  resetAt: string;
  models: UsageModel[];
  pricing: { label: string; inputPerMillion: number; outputPerMillion: number }[];
}

const FALLBACK = "사용량을 불러오지 못했습니다. 잠시 후 다시 시도하세요.";

function usd(value: number, digits = 4): string {
  return `$${value.toFixed(digits)}`;
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

  return (
    <section className="card" aria-labelledby="profile-usage-title">
      <h3 className="section-title" id="profile-usage-title">
        내 월 사용량
      </h3>
      {error && (
        <p className="callout callout-error" role="alert">
          {error}
        </p>
      )}
      {!data && !error && <p className="meta">불러오는 중...</p>}
      {data && (
        <>
          <p className="usage-month">{data.month} 누적 · USD</p>
          <p className="usage-total">{usd(data.totalUsd ?? 0)}</p>
          <p className="meta">다음 초기화: {formatKst(data.resetAt)} (한국 시간)</p>

          <div className="usage-list">
            {(data.models ?? []).length === 0 && <p className="meta">이번 달에는 아직 사용 기록이 없습니다.</p>}
            {(data.models ?? []).map((m) => (
              <div className="usage-row" key={m.modelId}>
                <div>
                  <strong>{m.label}</strong>
                  <div className="sub">
                    {m.calls}회 · 입력 {m.inputTokens.toLocaleString("ko-KR")} / 출력{" "}
                    {m.outputTokens.toLocaleString("ko-KR")} 토큰
                  </div>
                </div>
                <strong>{m.priced ? usd(m.usd, 6) : "단가 없음"}</strong>
              </div>
            ))}
          </div>

          <p className="meta usage-note">
            AWS Bedrock 공개 단가와 실제 반환된 토큰 수로 계산한 예상 비용입니다. 실제 청구서와 차이가 있을 수 있습니다.
          </p>

          <details className="usage-pricing">
            <summary>모델별 단가 (USD / 100만 토큰)</summary>
            <table>
              <thead>
                <tr>
                  <th>모델</th>
                  <th>입력</th>
                  <th>출력</th>
                </tr>
              </thead>
              <tbody>
                {(data.pricing ?? []).map((p) => (
                  <tr key={p.label}>
                    <td>{p.label}</td>
                    <td>{p.inputPerMillion}</td>
                    <td>{p.outputPerMillion || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </>
      )}
    </section>
  );
}