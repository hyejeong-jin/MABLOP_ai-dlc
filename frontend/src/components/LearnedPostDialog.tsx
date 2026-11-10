// Shows the stored text of one learned post and lets the owner change its id (= title).
import { useEffect, useState, type FormEvent } from "react";

import { ApiRequestError, postJson } from "../api";
import { Modal } from "./Modal";

interface Props {
  postId: string;
  onClose: () => void;
  onRenamed: () => void;
}

const LOAD_ERROR = "포스트 내용을 불러오지 못했습니다.";
const RENAME_ERROR = "제목을 바꾸지 못했습니다. 잠시 후 다시 시도하세요.";

function messageOf(err: unknown, fallback: string): string {
  return err instanceof ApiRequestError && err.message ? err.message : fallback;
}

export function LearnedPostDialog({ postId, onClose, onRenamed }: Props) {
  const [currentId, setCurrentId] = useState(postId);
  const [draftTitle, setDraftTitle] = useState(postId);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let alive = true;
    setText(null);
    setError(null);
    postJson<{ postId: string; text: string }>({ action: "get-learning-post", postId: currentId })
      .then((res) => alive && setText(res.text))
      .catch((err) => alive && setError(messageOf(err, LOAD_ERROR)));
    return () => {
      alive = false;
    };
  }, [currentId]);

  async function rename(e: FormEvent) {
    e.preventDefault();
    const next = draftTitle.trim();
    if (!next || next === currentId || saving) return;
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await postJson({ action: "rename-learning-post", postId: currentId, newPostId: next });
      setCurrentId(next);
      setSaved(true);
      onRenamed();
    } catch (err) {
      setError(messageOf(err, RENAME_ERROR));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal title={currentId} onClose={onClose}>
      <form className="rename-form" onSubmit={rename}>
        <label className="field">
          포스트 제목 (ID)
          <input type="text" value={draftTitle} maxLength={120} onChange={(e) => setDraftTitle(e.target.value)} />
        </label>
        <button type="submit" className="btn-primary" disabled={saving || !draftTitle.trim() || draftTitle.trim() === currentId}>
          {saving ? "저장 중..." : "제목 변경"}
        </button>
      </form>
      {saved && (
        <p className="callout" role="status">
          제목을 바꿨어요.
        </p>
      )}
      {error && (
        <p className="callout callout-error" role="alert">
          {error}
        </p>
      )}
      {text === null && !error && <p className="meta">불러오는 중...</p>}
      {text !== null && <pre className="learned-text" aria-label="학습한 글 내용">{text}</pre>}
    </Modal>
  );
}