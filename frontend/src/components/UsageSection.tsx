// "내 월 사용량" on the 마이페이지: Bedrock cost per model and cumulative tokens.
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
  pricing: { label: string; inputPerMillion: number; outputPerMillion: number }[];
}

const FALLBACK = "사용량을 불러오지 못했습니다. 잠시 후 다시 시도하세요.";
const usd = (value: number, digits = 4) => `$${value.toFixed(digits)}`;
const count = (value: number) => value.toLocaleString("ko-KR");

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

  const totalTokens =
    data?.totalTokens ?? data?.models?.reduce((n, m) => n + m.inputTokens + m.outputTokens, 0) ?? 0;
  const models = data?.models ?? [];

  return (
    <>
      <section className="card usage-card" aria-labelledby="profile-usage-title">
        <div className="usage-header">
          <div>
            <h3 className="section-title section-accent" id="profile-usage-title">
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
                      <td colSpan={5} className="table-empty empty-state">
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

    </>
  );
}