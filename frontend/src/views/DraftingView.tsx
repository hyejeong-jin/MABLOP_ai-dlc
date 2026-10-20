// Drafting view (task 11.4). Title/outline/notes + image attach (opt. per-image desc).
// Design Principle 2: browser -> S3 direct (presigned PUT), only S3 keys to the Lambda
// (Req 3.1, 6.1). Resize/compress + 10-image cap via images.ts (Req 7.3, 7.4).
// Browser never ships bytes to the Lambda; NO Bedrock/AWS SDK (Req 6.1). YAGNI: local state.
import { useEffect, useState } from "react";
import { postJson, ApiRequestError } from "../api";
import { capImages, resizeImage, MAX_IMAGES } from "../images";

// shared token key (same as StyleLearningView) so the pre-shared token is reused.
const TOKEN_KEY = "mablop-token";

// one attached image + its optional user description (cost-0 caption path, Req 3.4).
interface Attached {
  file: File;
  description: string;
}

// issue-presigned-urls response (design: Interfaces).
interface PresignUpload {
  key: string;
  url: string;
  method: string;
}
interface PresignResponse {
  uploads: PresignUpload[];
}

// generate-draft response (design: Interfaces).
interface DraftResponse {
  draftId: string;
  markdown: string;
  draftKey?: string;
  topK?: number;
}

export function DraftingView() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) ?? "");
  const [title, setTitle] = useState("");
  const [outline, setOutline] = useState("");
  const [notes, setNotes] = useState("");
  const [images, setImages] = useState<Attached[]>([]);
  const [loading, setLoading] = useState(false);
  const [markdown, setMarkdown] = useState<string | undefined>(undefined);
  const [error, setError] = useState<string | undefined>(undefined);

  function updateToken(v: string) {
    setToken(v);
    localStorage.setItem(TOKEN_KEY, v); // persist shared token locally
  }

  // Native input + drop zone. Ignore non-images and cap at 10 (Req 7.4).
  function addFiles(files: File[]) {
    const picked = files
      .filter((file) => file.type.startsWith("image/"))
      .map((file) => ({ file, description: "" }));
    setImages((cur) => capImages([...cur, ...picked]));
  }

  function onFiles(e: React.ChangeEvent<HTMLInputElement>) {
    addFiles(Array.from(e.target.files ?? []));
    e.target.value = "";
  }

  function onDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    addFiles(Array.from(e.dataTransfer.files));
  }

  function setDescription(i: number, description: string) {
    setImages((cur) => cur.map((a, j) => (j === i ? { ...a, description } : a)));
  }

  function removeImage(i: number) {
    setImages((cur) => cur.filter((_, j) => j !== i));
  }

  // Upload flow: resize -> presign -> PUT bytes to S3 -> analyze -> generate.
  // Only keys ever reach the Lambda (Design Principle 2 / Req 3.1, 6.1).
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(undefined);
    setMarkdown(undefined);
    try {
      const capped = capImages(images); // defensive re-cap (Req 7.4)
      let imageKeys: string[] = [];

      if (capped.length) {
        // 1. compress each image client-side (Req 7.3).
        const blobs = await Promise.all(capped.map((a) => resizeImage(a.file)));

        // 2. ask the backend for presigned PUT urls (keys live under images/).
        const presign = await postJson<PresignResponse>(
          {
            action: "issue-presigned-urls",
            files: blobs.map((b, i) => ({
              filename: capped[i].file.name,
              contentType: b.type || "image/jpeg",
              size: b.size,
            })),
          },
          token,
        );

        // 3. PUT bytes straight to S3 -- raw fetch, bypassing api.ts (never to Lambda).
        await Promise.all(
          presign.uploads.map((u, i) =>
            fetch(u.url, {
              method: u.method || "PUT",
              headers: { "Content-Type": blobs[i].type || "image/jpeg" },
              body: blobs[i],
            }),
          ),
        );

        imageKeys = presign.uploads.map((u) => u.key);

        // 4. analyze-images: send keys (+ optional user description for cost-0 skip).
        await postJson(
          {
            action: "analyze-images",
            images: presign.uploads.map((u, i) => {
              const desc = capped[i].description.trim();
              return desc ? { key: u.key, description: desc } : { key: u.key };
            }),
          },
          token,
        );
      }

      // 5. generate-draft: keys only, synchronous return.
      const draft = await postJson<DraftResponse>(
        { action: "generate-draft", title, outline, notes, imageKeys },
        token,
      );
      setMarkdown(draft.markdown);
    } catch (err) {
      const msg =
        err instanceof ApiRequestError ? `${err.code}: ${err.message}` : String(err);
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section>
      <h1>초안 작성</h1>
      <form onSubmit={submit}>
        <label>
          토큰
          <input
            type="password"
            value={token}
            onChange={(e) => updateToken(e.target.value)}
          />
        </label>
        <label>
          제목
          <input value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label>
          개요
          <textarea value={outline} onChange={(e) => setOutline(e.target.value)} />
        </label>
        <label>
          메모
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} />
        </label>

        <fieldset>
          <legend>이미지 첨부 (최대 {MAX_IMAGES}장)</legend>
          <p aria-live="polite">선택된 이미지: {images.length}/{MAX_IMAGES}</p>
          <div
            role="group"
            aria-label="이미지 끌어놓기 영역"
            onDragOver={(e) => e.preventDefault()}
            onDrop={onDrop}
          >
            <label htmlFor="image-input">이미지 선택 또는 여기에 끌어놓기</label>
            <input
              id="image-input"
              type="file"
              aria-label="이미지 첨부"
              accept="image/*"
              multiple
              onChange={onFiles}
            />
          </div>
          {images.map((a, i) => (
            <div key={`${a.file.name}-${i}`}>
              <ImagePreview file={a.file} />
              <span>{a.file.name}</span>
              <input
                aria-label={`이미지 설명 ${i}`}
                value={a.description}
                onChange={(e) => setDescription(i, e.target.value)}
                placeholder="설명 (선택: 입력하면 비전 호출 생략)"
              />
              <button type="button" onClick={() => removeImage(i)}>
                제거
              </button>
            </div>
          ))}
        </fieldset>

        <button type="submit" disabled={loading}>
          초안 생성
        </button>
      </form>

      {/* synchronous result area (ResultView full impl in 11.6; keys-only path done) */}
      <DraftResult loading={loading} markdown={markdown} error={error} />
    </section>
  );
}

function ImagePreview({ file }: { file: File }) {
  const [src, setSrc] = useState("");

  useEffect(() => {
    if (!URL.createObjectURL) return;
    const url = URL.createObjectURL(file);
    setSrc(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  return src ? <img src={src} alt={file.name} width={96} height={96} /> : null;
}

// Prop-driven result renderer (mirrors ResultView's {loading, markdown?, error?} shape).
// Inlined here so 11.4 stays self-contained; 11.6 owns the shared ResultView.
function DraftResult(props: {
  loading: boolean;
  markdown?: string;
  error?: string;
}) {
  const { loading, markdown, error } = props;
  if (loading) return <p>초안 생성 중...</p>;
  if (error) return <p role="alert">{error}</p>;
  if (markdown) return <pre aria-label="초안 결과">{markdown}</pre>;
  return null;
}
