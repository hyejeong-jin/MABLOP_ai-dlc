// The "Bedrock 사용 권한" dropdown for members (Req 21.1, 21.3, 21.7, 21.9, 21.10).
// The view calls useGrants() once and passes the result in, so the view and this
// component share one selection. An admin gets nothing rendered.

import { useId } from "react";
import { CircleAlert, Info } from "lucide-react";
import { formatKstDate } from "../format";
import type { GrantOption, GrantsState } from "./useGrants";

export const GRANT_SELECT_LABEL = "Bedrock 사용 권한";
export const GRANT_SELECT_PLACEHOLDER = "선택하세요";
export const GRANT_SELECT_EMPTY_MESSAGE =
  "사용 가능한 Bedrock 사용 권한이 없습니다. 마이페이지에서 신청하세요.";

export function grantOptionLabel(grant: GrantOption): string {
  return `${GRANT_SELECT_LABEL} · ${formatKstDate(grant.decidedAt ?? undefined)} · ${formatKstDate(
    grant.expiresAt ?? undefined,
  )}`;
}

export interface GrantSelectProps {
  grants: GrantsState;
}

export function GrantSelect({ grants }: GrantSelectProps) {
  const selectId = useId();
  const { enabled, options, selectedId, select, loading, error } = grants;

  if (!enabled) return null;

  return (
    <div className="field-group">
      <label className="field" htmlFor={selectId}>
        {GRANT_SELECT_LABEL}
      </label>
      <select
        id={selectId}
        value={selectedId ?? ""}
        onChange={(e) => select(e.target.value === "" ? null : e.target.value)}
        disabled={loading}
      >
        <option value="">{GRANT_SELECT_PLACEHOLDER}</option>
        {options.map((grant) => (
          <option key={grant.id} value={grant.id}>
            {grantOptionLabel(grant)}
          </option>
        ))}
      </select>
      {error && (
        <div className="callout callout-error" role="alert">
          <CircleAlert size={16} aria-hidden="true" />
          <p>{error}</p>
        </div>
      )}
      {!loading && options.length === 0 && (
        <div className="callout" role="status">
          <Info size={16} aria-hidden="true" />
          <p>{GRANT_SELECT_EMPTY_MESSAGE}</p>
        </div>
      )}
    </div>
  );
}
