// Drafting view (task 11.4). Title/outline/notes + image attach (opt. per-image desc).
// Design Principle 2: browser -> S3 direct (presigned PUT), only S3 keys to the Lambda
// (Req 3.1, 6.1). Resize/compress + 10-image cap via images.ts (Req 7.3, 7.4).
// Browser never ships bytes to the Lambda; NO Bedrock/AWS SDK (Req 6.1). YAGNI: local state.
import { useEffect, useState } from "react";
import { CircleAlert, Check, Copy, Download, Info } from "lucide-react";
import { postJson, ApiRequestError } from "../api";
import { GrantSelect } from "../grants/GrantSelect";
import { useGrants } from "../grants/useGrants";
import { capImages, resizeImage, MAX_IMAGES } from "../images";

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
  generalStyle?: boolean;
}

// onDraftCreated hands the new draftId to App so the Results tab can focus it (Req 23.5).
export interface DraftingViewProps {
  onDraftCreated?: (draftId: string) => void;
}

export function DraftingView({ onDraftCreated }: DraftingViewProps = {}) {
  const grants = useGrants();
  const [title, setTitle] = useState("");
  const [outline, setOutline] = useState("");
  const [notes, setNotes] = useState("");
  const [images, setImages] = useState<Attached[]>([]);
  const [loading, setLoading] = useState(false);
  const [stage, setStage] = useState("");
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [markdown, setMarkdown] = useState<string | undefined>(undefined);
  const [generalStyle, setGeneralStyle] = useState(false);
  const [error, setError] = useState<string | undefined>(undefined);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!loading) {
      setElapsedSeconds(0);
      return;
    }
    const started = Date.now();
    const timer = window.setInterval(
      () => setElapsedSeconds(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [loading]);

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

  async function copyDraft(text: string) {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  }

  function downloadDraft(text: string) {
    const blob = new Blob([text], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "mablop-draft.md";
    link.click();
    URL.revokeObjectURL(url);
  }

  // Upload flow: resize -> presign -> PUT bytes to S3 -> analyze -> generate.
  // Only keys ever reach the Lambda (Design Principle 2 / Req 3.1, 6.1).
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setStage("준비 중...");
    setError(undefined);
    setMarkdown(undefined);
    setGeneralStyle(false);
    try {
      const capped = capImages(images); // defensive re-cap (Req 7.4)
      let imageKeys: string[] = [];

      if (capped.length) {
        setStage(`이미지 ${capped.length}장 압축 및 업로드 중...`);
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

        setStage("이미지 맥락 분석 중... (이미지 수에 따라 수분 걸릴 수 있습니다)");
        // 4. analyze-images: send keys (+ optional user description for cost-0 skip).
        await postJson(
          grants.withGrant({
            action: "analyze-images",
            images: presign.uploads.map((u, i) => {
              const desc = capped[i].description.trim();
              return desc ? { key: u.key, description: desc } : { key: u.key };
            }),
          }),
        );
      }

      setStage("내 문체로 초안을 쓰는 중... (자료 검색, 문체 분석, 본문 생성 순서로 진행합니다)");
      // 5. generate-draft: keys only, synchronous return.
      const draft = await postJson<DraftResponse>(
        grants.withGrant({ action: "generate-draft", title, outline, notes, imageKeys }),
      );
      setMarkdown(draft.markdown);
      setGeneralStyle(draft.generalStyle === true);
      onDraftCreated?.(draft.draftId);
    } catch (err) {
      // 403 forbidden: grant options are reloaded; show the backend message.
      if (grants.handleGrantError(err)) {
        setError(err instanceof ApiRequestError ? err.message : String(err));
        return;
      }
      const msg =
        err instanceof ApiRequestError ? `${err.code}: ${err.message}` : String(err);
      setError(msg);
    } finally {
      setLoading(false);
      setStage("");
    }
  }

  return (
    <section>
      <h2 className="page-title">초안 작성</h2>
      <p className="page-lead">제목과 개요, 메모를 적고 사진을 붙이면 학습한 문체로 초안을 만들어요. 2~5분쯤 걸립니다.</p>

      <form className="stack" onSubmit={submit}>
        <GrantSelect grants={grants} />
        <div className="field-group">
          <label className="field">
            제목
            <input value={title} onChange={(e) => setTitle(e.target.value)} aria-describedby="hint-title" />
          </label>
          <p id="hint-title" className="field-hint">블로그 포스트 제목을 작성해주세요.</p>
        </div>
        <div className="field-group">
          <label className="field">
            개요
            <textarea value={outline} onChange={(e) => setOutline(e.target.value)} aria-describedby="hint-outline" />
          </label>
          <p id="hint-outline" className="field-hint">어떤 내용을 작성할지 글의 개요를 개괄적으로 작성해주세요.</p>
        </div>
        <div className="field-group">
          <label className="field">
            메모
            <textarea value={notes} onChange={(e) => setNotes(e.target.value)} aria-describedby="hint-notes" />
          </label>
          <p id="hint-notes" className="field-hint">글을 어떻게 써내려갈지 방향성과 말투 등을 작성해주세요. 작성자 관점에서 AI에게 요청할 사항을 입력해도 됩니다.</p>
        </div>

        <fieldset>
          <legend>포스트에 삽입할 대표 이미지 첨부 (최대 {MAX_IMAGES}장)</legend>
          <p className="field-hint mono" aria-live="polite">선택된 이미지: {images.length}/{MAX_IMAGES}</p>
          <div
            className="dropzone"
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
            <div className="attach-row" key={`${a.file.name}-${i}`}>
              <ImagePreview file={a.file} />
              <div className="attach-body">
                <span className="attach-name">{a.file.name}</span>
                <input
                  aria-label={`이미지 설명 ${i}`}
                  value={a.description}
                  onChange={(e) => setDescription(i, e.target.value)}
                  placeholder="설명 (선택: 입력하면 비전 호출 생략)"
                />
              </div>
              <button type="button" className="btn-sm" onClick={() => removeImage(i)}>
                제거
              </button>
            </div>
          ))}
        </fieldset>

        <button type="submit" className="btn-primary btn-block" disabled={loading || !grants.canCallBedrock}>
          초안 생성
        </button>
      </form>

      <DraftResult
        loading={loading}
        stage={stage}
        elapsedSeconds={elapsedSeconds}
        markdown={markdown}
        generalStyle={generalStyle}
        error={error}
        copied={copied}
        onCopy={copyDraft}
        onDownload={downloadDraft}
      />
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

  return src ? <img src={src} alt={file.name} width={72} height={72} /> : <span aria-hidden="true" />;
}

// Prop-driven result renderer (mirrors ResultView's {loading, markdown?, error?} shape).
function DraftResult(props: {
  loading: boolean;
  stage: string;
  elapsedSeconds: number;
  markdown?: string;
  generalStyle?: boolean;
  error?: string;
  copied: boolean;
  onCopy: (text: string) => void;
  onDownload: (text: string) => void;
}) {
  const { loading, stage, elapsedSeconds, markdown, generalStyle, error, copied, onCopy, onDownload } = props;
  if (loading) {
    return (
      <div className="callout page-section" role="status">
        <Info size={16} aria-hidden="true" />
        <div>
          <p>초안 생성 중...</p>
          <p className="meta">{stage}</p>
          <p className="meta mono">경과 시간: {elapsedSeconds}초</p>
        </div>
      </div>
    );
  }
  if (error) {
    return (
      <div className="callout callout-error page-section" role="alert">
        <CircleAlert size={16} aria-hidden="true" />
        <p>{error}</p>
      </div>
    );
  }
  if (markdown) {
    return (
      <div className="draft-result-inline">
        <div className="draft-result-actions">
          <h3 className="section-title">생성된 초안</h3>
          <div>
            <button type="button" className="btn-sm btn-with-icon" onClick={() => onCopy(markdown)}>
              {copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
              {copied ? "복사됨" : "복사"}
            </button>
            <button type="button" className="btn-sm btn-with-icon" onClick={() => onDownload(markdown)}>
              <Download size={14} aria-hidden="true" />
              다운로드
            </button>
          </div>
        </div>
        {generalStyle && (
          <p className="callout" role="status">
            아직 학습한 글이 없어 일반 문체로 작성되었습니다. 스타일 학습을 마치면 내 문체가 반영됩니다.
          </p>
        )}
        <pre className="draft-output" aria-label="초안 결과">{markdown}</pre>
      </div>
    );
  }
  return null;
}