// VOC create/edit form. Field validation errors are shown next to each field;
// server errors are passed separately through `serverError`.
import { useEffect, useState, type FormEvent } from "react";
import { CircleAlert } from "lucide-react";
import {
  VOC_LIMITS,
  VOC_SOURCE_TYPES,
  type VocInput,
  type VocRecord,
  type VocSourceType,
} from "../api";

export interface VocFieldErrors {
  body?: string;
  sourceType?: string;
  tags?: string;
  note?: string;
}

export function parseTags(text: string): string[] {
  return text
    .split(/[,\n]/)
    .map((t) => t.trim())
    .filter(Boolean);
}

export function validateVoc(input: VocInput): VocFieldErrors {
  const errors: VocFieldErrors = {};
  if (!input.body.trim()) errors.body = "원문을 입력하세요.";
  else if (input.body.length > VOC_LIMITS.body)
    errors.body = `원문은 ${VOC_LIMITS.body.toLocaleString()}자 이하로 입력하세요.`;
  if (!VOC_SOURCE_TYPES.some((t) => t.value === input.sourceType))
    errors.sourceType = "출처 유형을 선택하세요.";
  if (input.tags.length > VOC_LIMITS.tags) errors.tags = `태그는 ${VOC_LIMITS.tags}개 이하로 입력하세요.`;
  else if (input.tags.some((t) => t.length > VOC_LIMITS.tagLength))
    errors.tags = `태그는 하나당 ${VOC_LIMITS.tagLength}자 이하로 입력하세요.`;
  if (input.note.length > VOC_LIMITS.note)
    errors.note = `메모는 ${VOC_LIMITS.note.toLocaleString()}자 이하로 입력하세요.`;
  return errors;
}

interface Props {
  initial?: VocRecord | null;
  busy?: boolean;
  serverError?: string | null;
  onSubmit: (input: VocInput) => void | Promise<void>;
  onCancel?: () => void;
}

export function VocEditor({ initial, busy = false, serverError, onSubmit, onCancel }: Props) {
  const [body, setBody] = useState(initial?.body ?? "");
  const [sourceType, setSourceType] = useState<VocSourceType>(
    initial?.userMetadata.sourceType ?? "customer",
  );
  const [tagsText, setTagsText] = useState(initial?.userMetadata.tags.join(", ") ?? "");
  const [note, setNote] = useState(initial?.userMetadata.note ?? "");
  const [errors, setErrors] = useState<VocFieldErrors>({});

  useEffect(() => {
    setBody(initial?.body ?? "");
    setSourceType(initial?.userMetadata.sourceType ?? "customer");
    setTagsText(initial?.userMetadata.tags.join(", ") ?? "");
    setNote(initial?.userMetadata.note ?? "");
    setErrors({});
  }, [initial]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const input: VocInput = { body, sourceType, tags: parseTags(tagsText), note };
    const found = validateVoc(input);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    void onSubmit(input);
  }

  const err = (id: string, msg?: string) =>
    msg ? (
      <p id={id} className="field-hint" role="alert" style={{ color: "var(--danger)" }}>
        {msg}
      </p>
    ) : null;

  return (
    <form className="stack" onSubmit={handleSubmit} noValidate aria-label={initial ? "의견 수정" : "새 의견 등록"}>
      <div className="field-group">
        <label className="field" htmlFor="voc-body">
          원문
          <textarea
            id="voc-body"
            rows={5}
            value={body}
            onChange={(e) => setBody(e.target.value)}
            aria-required="true"
            aria-invalid={errors.body ? true : undefined}
            aria-describedby={`voc-body-hint${errors.body ? " voc-body-error" : ""}`}
          />
        </label>
        <p id="voc-body-hint" className="field-hint">
          고객이 한 말이나 반응을 그대로 적어 두세요.
        </p>
        {err("voc-body-error", errors.body)}
      </div>

      <div className="field-group">
        <label className="field" htmlFor="voc-source-type">
          출처 유형
          <select
            id="voc-source-type"
            value={sourceType}
            onChange={(e) => setSourceType(e.target.value as VocSourceType)}
            aria-invalid={errors.sourceType ? true : undefined}
            aria-describedby={errors.sourceType ? "voc-source-type-error" : undefined}
          >
            {VOC_SOURCE_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
        {err("voc-source-type-error", errors.sourceType)}
      </div>

      <div className="field-group">
        <label className="field" htmlFor="voc-tags">
          태그
          <input
            id="voc-tags"
            type="text"
            value={tagsText}
            onChange={(e) => setTagsText(e.target.value)}
            aria-invalid={errors.tags ? true : undefined}
            aria-describedby={`voc-tags-hint${errors.tags ? " voc-tags-error" : ""}`}
          />
        </label>
        <p id="voc-tags-hint" className="field-hint">
          쉼표로 구분해 입력하세요. 예: 온보딩, 모바일
        </p>
        {err("voc-tags-error", errors.tags)}
      </div>

      <div className="field-group">
        <label className="field" htmlFor="voc-note">
          메모
          <textarea
            id="voc-note"
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            aria-invalid={errors.note ? true : undefined}
            aria-describedby={errors.note ? "voc-note-error" : undefined}
          />
        </label>
        {err("voc-note-error", errors.note)}
      </div>

      {serverError && (
        <div className="callout callout-error" role="alert">
          <CircleAlert size={16} aria-hidden="true" />
          <p>{serverError}</p>
        </div>
      )}

      <div className="status-actions">
        <button type="submit" className="btn-primary" disabled={busy}>
          {busy ? "저장 중..." : initial ? "수정 저장" : "의견 저장"}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel} disabled={busy}>
            취소
          </button>
        )}
      </div>
    </form>
  );
}
