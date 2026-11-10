// "비밀번호 찾기": there is no e-mail delivery in this service, so the administrator issues a
// temporary password. This dialog prepares the request mail and explains the steps.
import { useState } from "react";
import { Mail } from "lucide-react";

import { CONTACT_EMAIL } from "../config";
import { Modal } from "./Modal";

export function PasswordHelpDialog({ initialLoginId, onClose }: { initialLoginId: string; onClose: () => void }) {
  const [loginId, setLoginId] = useState(initialLoginId);
  const subject = encodeURIComponent("[MABLOP] 비밀번호 초기화 요청");
  const body = encodeURIComponent(
    `아이디: ${loginId.trim() || "(아이디를 입력해 주세요)"}\n\n비밀번호 초기화를 요청합니다.`,
  );

  return (
    <Modal title="비밀번호 찾기" onClose={onClose}>
      <p className="meta">
        MABLOP은 보안을 위해 비밀번호를 이메일로 보내지 않아요. 관리자가 확인한 뒤 임시 비밀번호를 발급해 드립니다.
      </p>
      <ol className="help-steps">
        <li>아래에 아이디를 입력하고 메일 보내기를 누르세요.</li>
        <li>관리자가 임시 비밀번호를 발급해 알려 드립니다.</li>
        <li>임시 비밀번호로 로그인한 뒤 마이페이지에서 새 비밀번호로 바꾸세요.</li>
      </ol>
      <label className="field">
        아이디
        <input
          type="text"
          value={loginId}
          maxLength={32}
          autoCapitalize="none"
          spellCheck={false}
          onChange={(e) => setLoginId(e.target.value)}
        />
      </label>
      <p className="help-actions">
        <a
          className="btn-link btn-link-primary"
          href={`mailto:${CONTACT_EMAIL}?subject=${subject}&body=${body}`}
        >
          <Mail size={16} aria-hidden="true" />
          관리자에게 메일 보내기
        </a>
      </p>
      <p className="meta">메일 주소: {CONTACT_EMAIL}</p>
    </Modal>
  );
}