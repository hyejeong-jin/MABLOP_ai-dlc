import { CircleCheck, CircleHelp, Sparkles, User } from "lucide-react";
import type { SourceLabel as SourceLabelKind } from "../api";

export const SOURCE_LABEL_INFO: Record<SourceLabelKind, { text: string; description: string }> = {
  fact: { text: "사실", description: "연결된 출처에 근거한 내용" },
  generated: { text: "생성", description: "모델이 요약·해석해 만든 내용" },
  "user-input": { text: "사용자 입력", description: "사용자가 직접 입력한 내용" },
  unverified: { text: "출처 미확인", description: "출처를 특정할 수 없어 사실로 볼 수 없음" },
};

const ICONS = {
  fact: CircleCheck,
  generated: Sparkles,
  "user-input": User,
  unverified: CircleHelp,
} as const;

interface SourceLabelProps {
  kind: SourceLabelKind;
  /** Show the plain-text description next to the badge (default true). */
  showDescription?: boolean;
}

/** Text-first badge: meaning never depends on color alone. */
export function SourceLabel({ kind, showDescription = true }: SourceLabelProps) {
  const info = SOURCE_LABEL_INFO[kind] ?? SOURCE_LABEL_INFO.unverified;
  const safeKind: SourceLabelKind = kind in SOURCE_LABEL_INFO ? kind : "unverified";
  const Icon = ICONS[safeKind];
  return (
    <span className="source-label-group" data-source-kind={safeKind}>
      <span className={`source-label source-label-${safeKind}`}>
        <Icon size={13} aria-hidden="true" />
        {info.text}
      </span>
      {showDescription ? (
        <span className="source-meta">{info.description}</span>
      ) : (
        <span className="sr-only">{info.description}</span>
      )}
    </span>
  );
}
