// Shared token field. The token lives in localStorage; the field is hidden once saved.
interface TokenFieldProps {
  token: string;
  show: boolean;
  onChange: (value: string) => void;
  onShow: () => void;
}

export function TokenField({ token, show, onChange, onShow }: TokenFieldProps) {
  if (!show) {
    return (
      <div className="token-saved">
        <span>저장된 토큰을 사용하고 있어요.</span>
        <button type="button" className="btn btn-quiet" onClick={onShow}>
          토큰 변경
        </button>
      </div>
    );
  }
  return (
    <label className="field">
      토큰
      <input type="password" value={token} onChange={(e) => onChange(e.target.value)} autoComplete="off" />
    </label>
  );
}