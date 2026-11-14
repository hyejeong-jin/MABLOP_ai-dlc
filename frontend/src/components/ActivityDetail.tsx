import { formatKst } from "../format";

export interface ActivityEvent {
  eventId: string;
  actorUserId?: string;
  type: string;
  occurredAt: string;
  count: number;
  relatedVocId?: string | null;
  relatedRunId?: string | null;
  metadata?: Record<string, unknown>;
}

export function activityTypeLabel(type: string): string {
  const labels: Record<string, string> = {
    "voc-created": "VOC 작성",
    "voc-updated": "VOC 수정",
    "voc-status-changed": "VOC 상태 변경",
    "research-run": "글 재료 수집",
    "research-started": "연구 시작",
    "research-completed": "연구 완료",
    "draft-created": "초안 작성",
    draft: "초안 작성",
    learned: "스타일 학습",
  };
  return labels[type] ?? type;
}

export function activityAccessibleName(event: ActivityEvent): string {
  return `${formatKst(event.occurredAt)} · ${activityTypeLabel(event.type)} · ${event.count}회`;
}

interface ActivityDetailProps {
  event: ActivityEvent;
  onClose?: () => void;
  /** Optional per-day summary such as "2025-01-02 · 초안 2회 · 학습 1회". */
  summary?: string;
}

/** A shared detail model for hover, keyboard focus, and touch activation. */
export function ActivityDetail({ event, onClose, summary }: ActivityDetailProps) {
  return (
    <aside
      id="profile-activity-detail"
      className="activity-detail"
      role="status"
      aria-live="polite"
      aria-labelledby="profile-activity-detail-title"
    >
      <div className="activity-detail-head">
        <strong id="profile-activity-detail-title">{activityTypeLabel(event.type)}</strong>
        {onClose && (
          <button type="button" className="btn-quiet btn-sm" onClick={onClose} aria-label="활동 상세 닫기">
            닫기
          </button>
        )}
      </div>
      {summary && <p className="activity-detail-summary">{summary}</p>}
      <dl className="activity-detail-list">
        <dt>일시</dt>
        <dd>{formatKst(event.occurredAt)}</dd>
        <dt>활동 수</dt>
        <dd>{event.count.toLocaleString("ko-KR")}회</dd>
        {(event.relatedVocId || event.relatedRunId) && (
          <>
            <dt>관련 식별자</dt>
            <dd className="mono">{event.relatedVocId ?? event.relatedRunId}</dd>
          </>
        )}
      </dl>
    </aside>
  );
}
