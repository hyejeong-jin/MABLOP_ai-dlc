// VOC list rendered as cards (works from 320px up). Status is always text.
import { VOC_STATUS_LABELS, type VocSummary } from "../api";
import { formatKst } from "../format";

interface Props {
  records: VocSummary[];
  busyId?: string | null;
  onEdit: (vocId: string) => void;
  onArchive: (vocId: string) => void;
}

export function VocList({ records, busyId, onEdit, onArchive }: Props) {
  if (records.length === 0) {
    return (
      <div className="empty-state" role="status">
        <p>조건에 맞는 의견이 없습니다.</p>
        <p className="field-hint">필터를 바꾸거나 새 의견을 등록해 보세요.</p>
      </div>
    );
  }
  return (
    <ul className="voc-record-list" aria-label="의견 목록">
      {records.map((r) => {
        const archived = r.status === "archived";
        return (
          <li key={r.vocId} className="voc-record">
            <div className="voc-record-header">
              <span className="meta">
                상태: <strong>{VOC_STATUS_LABELS[r.status] ?? r.status}</strong>
              </span>
              <span className="meta">{formatKst(r.createdAt)}</span>
            </div>
            <p className="voc-record-body">{r.preview}</p>
            <div className="status-actions">
              <button type="button" className="btn-sm" onClick={() => onEdit(r.vocId)} disabled={busyId === r.vocId}>
                수정
              </button>
              {!archived && (
                <button
                  type="button"
                  className="btn-sm btn-danger"
                  onClick={() => onArchive(r.vocId)}
                  disabled={busyId === r.vocId}
                >
                  보관
                </button>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
