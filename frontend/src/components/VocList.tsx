// 문의 게시판 표. 제목을 누르면 FAQ처럼 아래로 펼쳐진다. 좁은 화면에서는 CSS로 행 카드가 된다.
import type { ReactNode } from "react";
import { VOC_CATEGORY_LABELS, VOC_STATUS_LABELS, type VocSummary } from "../api";
import { formatKstDate } from "../format";

export interface VocRow {
  no: number;
  record: VocSummary;
}

interface Props {
  rows: VocRow[];
  showOwner?: boolean;
  expandedId: string | null;
  onToggle: (vocId: string) => void;
  renderDetail: (record: VocSummary) => ReactNode;
}

export function VocList({ rows, showOwner = false, expandedId, onToggle, renderDetail }: Props) {
  const cols = showOwner ? 6 : 5;
  return (
    <table className="vb-table" aria-label="문의 목록">
      <thead>
        <tr>
          <th scope="col" className="vb-col-no">번호</th>
          <th scope="col" className="vb-col-cat">분류</th>
          <th scope="col">제목</th>
          {showOwner && <th scope="col" className="vb-col-owner">작성자</th>}
          <th scope="col" className="vb-col-date">작성일</th>
          <th scope="col" className="vb-col-status">상태</th>
        </tr>
      </thead>
      {rows.map(({ no, record: r }) => {
        const open = expandedId === r.vocId;
        const panelId = `voc-detail-${r.vocId}`;
        return (
          <tbody key={r.vocId} className={open ? "vb-item is-open" : "vb-item"}>
            <tr className="vb-row">
              <td className="vb-col-no">{no}</td>
              <td className="vb-col-cat">{VOC_CATEGORY_LABELS[r.category] ?? r.category}</td>
              <td className="vb-title">
                <button
                  type="button"
                  className="vb-title-btn"
                  aria-expanded={open}
                  aria-controls={panelId}
                  onClick={() => onToggle(r.vocId)}
                >
                  {r.title || r.preview}
                </button>
              </td>
              {showOwner && (
                <td className="vb-col-owner" data-label="작성자">
                  {r.ownerLoginId ?? ""}
                </td>
              )}
              <td className="vb-col-date">{formatKstDate(r.createdAt)}</td>
              <td className="vb-col-status">{VOC_STATUS_LABELS[r.status] ?? r.status}</td>
            </tr>
            {r.hasReply && (
              <tr className="vb-re-row">
                <td colSpan={cols}>
                  <span className="vb-re">Re: 운영자 답변</span>
                </td>
              </tr>
            )}
            {open && (
              <tr className="vb-detail-row">
                <td colSpan={cols}>
                  <div id={panelId} className="vb-detail">
                    {renderDetail(r)}
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        );
      })}
    </table>
  );
}
