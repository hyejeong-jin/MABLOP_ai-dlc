import { useEffect, useMemo, useState } from "react";

import { ApiRequestError, postJson, type ActivityResponse } from "../api";

interface ActivityBucket {
  date: string;
  total: number;
  byType: Record<string, number>;
  eventIds: string[];
}

export type ActivityLevel = 0 | 1 | 2 | 3 | 4;

const YEAR_DAYS = 365;
const DAY_MS = 24 * 60 * 60 * 1000;
const FALLBACK = "활동을 불러오지 못했습니다. 잠시 후 다시 시도하세요.";
const KST_DATE = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/** Last 365 days in Korea time, oldest first (YYYY-MM-DD). */
export function yearDates(now: Date = new Date()): string[] {
  const today = KST_DATE.format(now);
  const base = new Date(`${today}T12:00:00+09:00`).getTime();
  const dates: string[] = [];
  for (let i = YEAR_DAYS - 1; i >= 0; i -= 1) {
    dates.push(KST_DATE.format(new Date(base - i * DAY_MS)));
  }
  return dates;
}

/** Same thresholds as the earlier GitHub-style calendar: 0, 1, 2, 3-4, 5+. */
export function levelOf(total: number): ActivityLevel {
  if (total <= 0) return 0;
  if (total === 1) return 1;
  if (total === 2) return 2;
  if (total <= 4) return 3;
  return 4;
}

/** "YYYY-MM-DD · 초안 N회 · 학습 N회" (other types are appended with their counts). */
export function dayLabel(date: string, byType: Record<string, number> = {}): string {
  const parts = [`초안 ${byType.draft ?? 0}회`, `학습 ${byType.learned ?? 0}회`];
  for (const [type, count] of Object.entries(byType)) {
    if (type === "draft" || type === "learned") continue;
    parts.push(`${typeLabel(type)} ${count}회`);
  }
  return [date, ...parts].join(" · ");
}

function typeLabel(type: string): string {
  const labels: Record<string, string> = {
    draft: "초안 작성",
    learned: "스타일 학습",
    "voc-created": "목소리함 의견",
    "voc-updated": "목소리함 수정",
    "voc-status-changed": "목소리함 상태 변경",
    "research-run": "글 재료 수집",
  };
  return labels[type] ?? type;
}

const MONTHS = ["1월", "2월", "3월", "4월", "5월", "6월", "7월", "8월", "9월", "10월", "11월", "12월"];

export function ActivitySection() {
  const dates = useMemo(() => yearDates(), []);
  const [data, setData] = useState<ActivityResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    postJson<ActivityResponse>({
      action: "get-activity",
      from: dates[0],
      to: dates[dates.length - 1],
      granularity: "day",
    })
      .then((response) => alive && setData(response))
      .catch((err) => alive && setError(err instanceof ApiRequestError && err.message ? err.message : FALLBACK));
    return () => {
      alive = false;
    };
  }, [dates]);

  const buckets = useMemo(() => {
    const map = new Map<string, ActivityBucket>();
    for (const bucket of (data?.daily ?? []) as unknown as ActivityBucket[]) {
      map.set(bucket.date, bucket);
    }
    return map;
  }, [data]);

  // Grid: 7 rows (Sunday first), one column per week. Leading blanks align the first day.
  const lead = new Date(`${dates[0]}T12:00:00+09:00`).getUTCDay();
  const cells: (string | null)[] = [...Array<null>(lead).fill(null), ...dates];
  while (cells.length % 7 !== 0) cells.push(null);
  const columns = cells.length / 7;

  // Month label above the first column that contains the 1st of a month (or the first column).
  const monthLabels: { column: number; text: string }[] = [];
  cells.forEach((date, index) => {
    if (!date) return;
    const [, month, day] = date.split("-").map(Number);
    if (day === 1 || index === lead) {
      const column = Math.floor(index / 7);
      if (!monthLabels.some((label) => label.column === column)) {
        monthLabels.push({ column, text: MONTHS[month - 1] });
      }
    }
  });

  const activeDays = dates.filter((date) => (buckets.get(date)?.total ?? 0) > 0).length;
  const totalActions = dates.reduce((sum, date) => sum + (buckets.get(date)?.total ?? 0), 0);
  const shown = selected ?? hovered;
  const shownBucket = shown ? buckets.get(shown) : undefined;
  const shownLabel = shown ? dayLabel(shown, shownBucket?.byType) : null;

  return (
    <section className="card activity-section" aria-labelledby="profile-activity-title">
      <div className="usage-header">
        <div>
          <h3 className="section-title section-accent" id="profile-activity-title">포스트 작성 활동</h3>
          <p className="meta">최근 1년 동안 초안 작성과 스타일 학습을 날짜별로 보여줘요.</p>
        </div>
      </div>

      {error && <p className="callout callout-error" role="alert">{error}</p>}
      {!data && !error && <p className="meta" role="status">불러오는 중...</p>}

      {data && (
        <>
          <p className="activity-summary">
            최근 1년 중 <strong>{activeDays.toLocaleString("ko-KR")}</strong>일 활동 · 총 <strong>{totalActions.toLocaleString("ko-KR")}</strong>회
          </p>

          <div className="activity-year" role="group" aria-label="최근 1년 활동 달력">
            <div className="activity-year-months" style={{ gridTemplateColumns: `repeat(${columns}, 11px)` }} aria-hidden="true">
              {Array.from({ length: columns }, (_, column) => {
                const label = monthLabels.find((item) => item.column === column);
                return <span key={column} style={{ gridColumn: column + 1 }}>{label?.text ?? ""}</span>;
              })}
            </div>
            <div className="activity-year-grid" style={{ gridTemplateColumns: `repeat(${columns}, 11px)` }}>
              {cells.map((date, index) => {
                if (!date) return <span key={`pad-${index}`} className="activity-year-pad" aria-hidden="true" />;
                const bucket = buckets.get(date);
                const total = bucket?.total ?? 0;
                const label = dayLabel(date, bucket?.byType);
                return (
                  <button
                    key={date}
                    type="button"
                    className="activity-year-cell"
                    style={{ gridColumn: Math.floor(index / 7) + 1, gridRow: (index % 7) + 1, background: `var(--heat-${levelOf(total)})` }}
                    aria-label={label}
                    title={label}
                    aria-pressed={selected === date}
                    onPointerEnter={() => setHovered(date)}
                    onPointerLeave={() => setHovered(null)}
                    onFocus={() => setHovered(date)}
                    onBlur={() => setHovered(null)}
                    onClick={() => setSelected((current) => (current === date ? null : date))}
                  />
                );
              })}
            </div>
            <div className="activity-legend" aria-label="활동량 범례">
              <span>적음</span>
              {([0, 1, 2, 3, 4] as const).map((level) => (
                <i key={level} aria-hidden="true" style={{ background: `var(--heat-${level})` }} />
              ))}
              <span>많음</span>
            </div>
          </div>

          <p className="activity-detail-line" role="status" aria-live="polite">
            {shownLabel ?? "날짜 칸에 마우스를 올리거나 누르면 그날의 활동이 보여요."}
          </p>
        </>
      )}
    </section>
  );
}