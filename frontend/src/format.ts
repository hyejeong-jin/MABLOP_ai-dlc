const KST_OFFSET_MS = 9 * 60 * 60 * 1000;

/** ISO 시각을 KST 날짜+시간(ko-KR, medium/short)으로 표시한다. 빈 값은 "", 해석 불가 값은 원문을 반환한다. */
export function formatKst(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

/** ISO 시각을 KST(UTC+9) 기준 `YYYY-MM-DD`로 표시한다. 빈 값은 "", 해석 불가 값은 원문을 반환한다. */
export function formatKstDate(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Date(date.getTime() + KST_OFFSET_MS).toISOString().slice(0, 10);
}

/** ISO 시각을 KST(UTC+9) 기준 `YYYY-MM-DD HH:mm`으로 표시한다. 빈 값은 "", 해석 불가 값은 원문을 반환한다. */
export function formatKstDateTime(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const iso = new Date(date.getTime() + KST_OFFSET_MS).toISOString();
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)}`;
}
