import { useEffect, useRef, useState } from "react";

import { ApiRequestError, postJson, type ActivityResponse } from "../api";
import { formatKst } from "../format";
import { ActivityDetail, activityAccessibleName, activityTypeLabel, type ActivityEvent } from "./ActivityDetail";

interface ActivityBucket {
  date: string;
  hour?: number;
  total: number;
  byType: Record<string, number>;
  eventIds: string[];
}

const FALLBACK = "활동을 불러오지 못했습니다. 잠시 후 다시 시도하세요.";
const PERIODS = [30, 90] as const;

function isoDateDaysAgo(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() - days + 1);
  return date.toISOString().slice(0, 10);
}

function eventForId(events: ActivityEvent[], id: string | undefined): ActivityEvent | undefined {
  return id ? events.find((event) => event.eventId === id) : undefined;
}

function eventCount(event: ActivityEvent): number {
  return Number.isFinite(event.count) && event.count > 0 ? event.count : 1;
}

function bucketEventFor(events: ActivityEvent[], bucket: ActivityBucket): ActivityEvent {
  const first = eventForId(events, bucket.eventIds[0]);
  const [type] = Object.entries(bucket.byType ?? {}).sort((a, b) => b[1] - a[1]);
  return {
    ...(first ?? { actorUserId: "", relatedVocId: null, relatedRunId: null }),
    eventId: `activity-${bucket.date}${bucket.hour == null ? "" : `-${bucket.hour}`}`,
    type: type?.[0] ?? first?.type ?? "activity",
    occurredAt: first?.occurredAt ?? `${bucket.date}T12:00:00+09:00`,
    count: bucket.total,
  };
}

/** "YYYY-MM-DD · 초안 N회 · 학습 N회" (other types appended with their labels). */
function bucketLabel(bucket: ActivityBucket): string {
  const by = bucket.byType ?? {};
  const parts = [`초안 ${by.draft ?? 0}회`, `학습 ${by.learned ?? 0}회`];
  for (const [type, count] of Object.entries(by)) {
    if (type !== "draft" && type !== "learned") parts.push(`${activityTypeLabel(type)} ${count}회`);
  }
  return [bucket.date, ...parts].join(" · ");
}

export function ActivitySection() {
  const [days, setDays] = useState<(typeof PERIODS)[number]>(30);
  const [data, setData] = useState<ActivityResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | undefined>();
  const [hoveredId, setHoveredId] = useState<string | undefined>();
  const suppressClickRef = useRef(false);

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    setSelectedId(undefined);
    setHoveredId(undefined);
    postJson<ActivityResponse>({
      action: "get-activity",
      from: isoDateDaysAgo(days),
      to: new Date().toISOString().slice(0, 10),
      granularity: "day",
    })
      .then((response) => alive && setData(response))
      .catch((err) => alive && setError(err instanceof ApiRequestError && err.message ? err.message : FALLBACK));
    return () => {
      alive = false;
    };
  }, [days]);

  const events = (data?.events ?? []) as unknown as ActivityEvent[];
  const daily = (data?.daily ?? []) as unknown as ActivityBucket[];
  const selectedOrHoveredId = selectedId ?? hoveredId;
  const selectedBucket = daily.find((bucket) => `activity-${bucket.date}${bucket.hour == null ? "" : `-${bucket.hour}`}` === selectedOrHoveredId);
  const detailEvent = eventForId(events, selectedOrHoveredId) ?? (selectedBucket ? bucketEventFor(events, selectedBucket) : undefined);
  const maxDaily = Math.max(1, ...daily.map((bucket) => bucket.total));
  function showHover(event: ActivityEvent) {
    setHoveredId(event.eventId);
  }

  function toggleSelected(event: ActivityEvent) {
    setSelectedId((current) => (current === event.eventId ? undefined : event.eventId));
    setHoveredId(undefined);
  }

  function activateFromKeyboard(event: ActivityEvent, key: string) {
    if (key !== "Enter" && key !== " ") return;
    suppressClickRef.current = true;
    window.setTimeout(() => { suppressClickRef.current = false; }, 0);
    toggleSelected(event);
  }

  function handleClick(event: ActivityEvent) {
    if (suppressClickRef.current) {
      suppressClickRef.current = false;
      return;
    }
    toggleSelected(event);
  }

  return (
    <section className="card activity-section" aria-labelledby="profile-activity-title">
      <div className="usage-header">
        <div>
          <h3 className="section-title section-accent" id="profile-activity-title">포스트 작성 활동</h3>
          <p className="meta">초안 작성, 스타일 학습, 목소리함, 글 재료 수집을 날짜별로 보여줘요. 색깔 박스에 마우스를 올리면 날짜와 횟수가 보여요.</p>
        </div>
        <label className="activity-period">
          <span className="sr-only">활동 기간</span>
          <select
            value={days}
            aria-describedby="profile-activity-period-hint"
            onChange={(event) => setDays(Number(event.target.value) as (typeof PERIODS)[number])}
          >
            {PERIODS.map((period) => <option key={period} value={period}>최근 {period}일</option>)}
          </select>
          <span className="sr-only" id="profile-activity-period-hint">활동 차트와 목록에 표시할 기간을 선택합니다.</span>
        </label>
      </div>

      {error && <p className="callout callout-error" role="alert">{error}</p>}
      {!data && !error && <p className="meta" role="status">불러오는 중...</p>}
      {data && events.length === 0 && (
        <div className="activity-empty" role="status">
          <strong>선택한 기간에 활동이 없습니다.</strong>
          <span>기간을 늘려 다른 활동이 있는지 확인해 보세요.</span>
        </div>
      )}
      {data && events.length > 0 && (
        <>
          <div
            className="activity-desktop"
            role="group"
            aria-label="활동 시각화"
            aria-describedby="profile-activity-chart-description"
          >
            <p className="sr-only" id="profile-activity-chart-description">
              스캐터 플롯과 일별 히트맵입니다. 각 점과 셀을 선택하면 날짜, 활동 유형, 활동 수와 관련 식별자를 확인할 수 있습니다.
            </p>
            <div className="activity-scatter" role="group" aria-label="활동 스캐터 플롯">
              {events.map((event, index) => (
                <button
                  key={event.eventId}
                  type="button"
                  className={`activity-point type-${event.type} ${selectedId === event.eventId ? "is-selected" : ""}`}
                  style={{ left: `${((index + 1) / (events.length + 1)) * 92 + 4}%`, bottom: `${18 + (eventCount(event) / maxDaily) * 64}%` }}
                  aria-label={activityAccessibleName(event)}
                  title={activityAccessibleName(event)}
                  aria-expanded={selectedId === event.eventId || hoveredId === event.eventId}
                  aria-controls="profile-activity-detail"
                  onPointerEnter={() => showHover(event)}
                  onPointerLeave={() => setHoveredId(undefined)}
                  onFocus={() => showHover(event)}
                  onBlur={() => setHoveredId(undefined)}
                  onKeyDown={(keyboardEvent) => activateFromKeyboard(event, keyboardEvent.key)}
                  onClick={() => handleClick(event)}
                >
                  <span aria-hidden="true" />
                </button>
              ))}
            </div>
            <div className="activity-axis"><span>과거</span><span>최근</span></div>

            <div className="activity-heatmap" role="group" aria-label="일별 활동 히트맵">
              {daily.map((bucket) => {
                const bucketEvent = bucketEventFor(events, bucket);
                const label = bucketLabel(bucket);
                return (
                  <button
                    key={bucket.date}
                    type="button"
                    className={`activity-cell level-${Math.min(4, Math.ceil((bucket.total / maxDaily) * 4))}`}
                    aria-label={label}
                    aria-expanded={selectedId === bucketEvent.eventId || hoveredId === bucketEvent.eventId}
                    title={label}
                    onPointerEnter={() => showHover(bucketEvent)}
                    onPointerLeave={() => setHoveredId(undefined)}
                    onFocus={() => showHover(bucketEvent)}
                    onBlur={() => setHoveredId(undefined)}
                    onKeyDown={(keyboardEvent) => activateFromKeyboard(bucketEvent, keyboardEvent.key)}
                    onClick={() => handleClick(bucketEvent)}
                  >
                    <span>{bucket.total}</span>
                  </button>
                );
              })}
            </div>
            <div className="activity-legend" aria-label="활동량 범례">
              <span>적음</span>
              <i className="level-1" aria-hidden="true" />
              <i className="level-2" aria-hidden="true" />
              <i className="level-3" aria-hidden="true" />
              <i className="level-4" aria-hidden="true" />
              <span>많음</span>
            </div>
          </div>

          <div className="activity-mobile" aria-label="활동 목록">
            <ul className="activity-list">
              {[...events].reverse().map((event) => (
                <li key={event.eventId}>
                  <button
                    type="button"
                    aria-label={activityAccessibleName(event)}
                    aria-expanded={selectedId === event.eventId || hoveredId === event.eventId}
                  aria-controls="profile-activity-detail"
                    onPointerEnter={() => showHover(event)}
                    onPointerLeave={() => setHoveredId(undefined)}
                    onFocus={() => showHover(event)}
                    onBlur={() => setHoveredId(undefined)}
                    onKeyDown={(keyboardEvent) => activateFromKeyboard(event, keyboardEvent.key)}
                    onClick={() => handleClick(event)}
                  >
                    <strong>{activityTypeLabel(event.type)}</strong>
                    <span>{formatKst(event.occurredAt)} · {eventCount(event)}회</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
          <p className="activity-summary">총 {events.reduce((total, event) => total + eventCount(event), 0).toLocaleString("ko-KR")}회 · {events.length}개 활동</p>
          {detailEvent && <ActivityDetail event={detailEvent} summary={selectedBucket ? bucketLabel(selectedBucket) : undefined} onClose={() => { setSelectedId(undefined); setHoveredId(undefined); }} />}
        </>
      )}
    </section>
  );
}
