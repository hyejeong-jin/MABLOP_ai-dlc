// 문의 작성/수정 폼: 분류, 제목, 내용. 필드 검증 오류는 각 필드 아래에, 서버 오류는 serverError로 따로 표시한다.
import { useEffect, useState, type FormEvent } from "react";
import { CircleAlert } from "lucide-react";
import { VOC_CATEGORY_LABELS, VOC_LIMITS, type VocCategory, type VocInput, type VocRecord } from "../api";

export interface VocFieldErrors {
  category?: string;
  title?: string;
  body?: string;
}

const CATEGORIES = Object.keys(VOC_CATEGORY_LABELS) as VocCategory[];

export function validateVoc(input: VocInput): VocFieldErrors {
  const errors: VocFieldErrors = {};
  if (!CATEGORIES.includes(input.category)) errors.category = "분류를 선택하세요.";
  if (!input.title.trim()) errors.title = "제목을 입력하세요.";
  else if (input.title.length > VOC_LIMITS.title) errors.title = `제목은 ${VOC_LIMITS.title}자 이하로 입력하세요.`;
  if (!input.body.trim()) errors.body = "내용을 입력하세요.";
  else if (input.body.length > VOC_LIMITS.body)
    errors.body = `내용은 ${VOC_LIMITS.body.toLocaleString()}자 이하로 입력하세요.`;
  return errors;
}

interface Props {
  initial?: VocRecord | null;
  idPrefix?: string;
  busy?: boolean;
  serverError?: string | null;
  onSubmit: (input: VocInput) => void | Promise<void>;
  onCancel?: () => void;
}

export function VocEditor({ initial, idPrefix = "voc", busy = false, serverError, onSubmit, onCancel }: Props) {
  const [category, setCategory] = useState<VocCategory>(initial?.category ?? "inquiry");
  const [title, setTitle] = useState(initial?.title ?? "");
  const [body, setBody] = useState(initial?.body ?? "");
  const [errors, setErrors] = useState<VocFieldErrors>({});

  useEffect(() => {
    setCategory(initial?.category ?? "inquiry");
    setTitle(initial?.title ?? "");
    setBody(initial?.body ?? "");
    setErrors({});
  }, [initial]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const input: VocInput = { category, title, body };
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
    <form className="vb-form" onSubmit={handleSubmit} noValidate aria-label={initial ? "문의 수정" : "문의 작성"}>
      <div className="field-group">
        <label className="field" htmlFor={`${idPrefix}-category`}>
          분류
        </label>
        <select
          id={`${idPrefix}-category`}
          value={category}
          onChange={(e) => setCategory(e.target.value as VocCategory)}
          aria-invalid={errors.category ? true : undefined}
        >
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {VOC_CATEGORY_LABELS[c]}
            </option>
          ))}
        </select>
        {err(`${idPrefix}-category-error`, errors.category)}
      </div>

      <div className="field-group">
        <label className="field" htmlFor={`${idPrefix}-title`}>
          제목
        </label>
        <input
          id={`${idPrefix}-title`}
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          aria-required="true"
          aria-invalid={errors.title ? true : undefined}
          aria-describedby={errors.title ? `${idPrefix}-title-error` : undefined}
        />
        {err(`${idPrefix}-title-error`, errors.title)}
      </div>

      <div className="field-group">
        <label className="field" htmlFor={`${idPrefix}-body`}>
          내용
        </label>
        <textarea
          id={`${idPrefix}-body`}
          rows={6}
          value={body}
          onChange={(e) => setBody(e.target.value)}
          aria-required="true"
          aria-invalid={errors.body ? true : undefined}
          aria-describedby={errors.body ? `${idPrefix}-body-error` : undefined}
        />
        {err(`${idPrefix}-body-error`, errors.body)}
      </div>

      {serverError && (
        <div className="callout callout-error" role="alert">
          <CircleAlert size={16} aria-hidden="true" />
          <p>{serverError}</p>
        </div>
      )}

      <div className="vb-actions">
        <button type="submit" className="btn-primary" disabled={busy}>
          {busy ? "저장 중..." : initial ? "수정 저장" : "등록"}
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
