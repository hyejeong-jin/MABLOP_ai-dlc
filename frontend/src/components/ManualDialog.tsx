// "사용자 매뉴얼": a short guide for first-time users.
import { Modal } from "./Modal";

const STEPS: { title: string; body: string }[] = [
  {
    title: "1. 가입과 승인",
    body: "로그인 화면의 '가입 신청'에서 아이디, 이름, 비밀번호(10~128자)를 입력합니다. 관리자가 승인하면 로그인할 수 있습니다.",
  },
  {
    title: "2. Bedrock 사용 권한 받기",
    body: "'마이페이지' 탭에서 사용 권한을 신청합니다. 관리자가 승인하면 학습과 초안 작성 화면의 '사용 권한' 목록에서 선택할 수 있습니다. 관리자 계정은 신청 없이 쓸 수 있습니다.",
  },
  {
    title: "3. 스타일 학습",
    body: "'스타일 학습' 탭에 내 네이버 블로그 주소를 넣고 학습합니다. 자동 수집이 막히면 글 본문을 붙여넣을 수 있습니다. 글이 쌓일수록 문체가 더 정확해집니다.",
  },
  {
    title: "4. 초안 작성",
    body: "'초안 작성' 탭에서 제목, 개요, 메모를 입력하고 필요하면 사진을 첨부합니다. 학습한 글이 없어도 작성할 수 있지만, 이 경우 내 문체가 아닌 일반 문체로 작성됩니다.",
  },
  {
    title: "5. 결과 보기",
    body: "'결과' 탭의 왼쪽 목록에서 지난 초안을 고르면 오른쪽에서 볼 수 있습니다. 필요 없는 초안은 삭제할 수 있습니다.",
  },
  {
    title: "6. 사용량 확인",
    body: "'마이페이지'의 '내 월 사용량'에서 이번 달 모델별 사용량과 예상 비용을 볼 수 있습니다.",
  },
];

export function ManualDialog({ onClose }: { onClose: () => void }) {
  return (
    <Modal title="사용자 매뉴얼" onClose={onClose}>
      <ol className="manual-list">
        {STEPS.map((step) => (
          <li key={step.title}>
            <h3>{step.title}</h3>
            <p>{step.body}</p>
          </li>
        ))}
      </ol>
      <p className="meta">문의는 개발자 hyejeong.jin에게 해 주세요.</p>
    </Modal>
  );
}