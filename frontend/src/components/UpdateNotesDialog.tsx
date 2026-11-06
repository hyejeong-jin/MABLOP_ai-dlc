// "업데이트 노트": release history, newest first.
import { Modal } from "./Modal";

interface Release {
  version: string;
  date: string;
  title: string;
  items: string[];
}

export const RELEASES: Release[] = [
  {
    version: "v1.4.0",
    date: "2026.11.07",
    title: "화면 구성 정리",
    items: [
      "기능 메뉴를 다시 상단 가로 탭으로 되돌렸습니다.",
      "'내 정보'가 '마이페이지'가 되었고, 내 월 사용량을 여기서 볼 수 있습니다.",
      "내 아이디와 로그아웃은 화면 왼쪽 아래, 사용자 매뉴얼과 업데이트 노트는 오른쪽 위 도움말 아이콘에 있습니다.",
    ],
  },
  {
    version: "v1.3.0",
    date: "2026.11.07",
    title: "학습 없이도 초안 작성",
    items: [
      "스타일 학습을 하지 않아도 초안을 작성할 수 있습니다. 이 경우 일반 문체로 작성되며 안내 문구가 표시됩니다.",
    ],
  },
  {
    version: "v1.2.0",
    date: "2026.11.07",
    title: "사이드 메뉴와 사용량 보기",
    items: [
      "왼쪽 사이드 메뉴 추가: 내 공간 표시, 사용자 매뉴얼, 업데이트 노트",
      "내 월 사용량 화면 추가: 모델별 사용량과 예상 비용",
      "MABLOP 로고 적용과 화면 정리",
    ],
  },
  {
    version: "v1.1.0",
    date: "2026.11.06",
    title: "관리자 계정 삭제",
    items: [
      "관리자가 사용자 계정과 데이터를 완전히 삭제할 수 있습니다. 같은 아이디로 다시 가입할 수 있습니다.",
      "로그인과 가입 화면의 입력칸 크기를 통일했습니다.",
    ],
  },
  {
    version: "v1.0.0",
    date: "2026.11.06",
    title: "사용자 계정과 관리자 기능",
    items: [
      "로그인, 가입 신청과 관리자 승인",
      "사용자별로 분리된 학습 데이터와 초안",
      "Bedrock 사용 권한 신청과 승인",
      "결과 탭에서 지난 초안 기록 보기",
    ],
  },
];

export function UpdateNotesDialog({ onClose }: { onClose: () => void }) {
  return (
    <Modal title="업데이트 노트" onClose={onClose}>
      <p className="meta">새로운 기능과 개선사항을 확인하세요. 왼쪽 메뉴에서 언제든 다시 볼 수 있습니다.</p>
      <div className="release-list">
        {RELEASES.map((r) => (
          <article className="release" key={r.version}>
            <div className="release-head">
              <span className="badge badge-active">{r.version}</span>
              <span className="meta">{r.date}</span>
            </div>
            <h3>{r.title}</h3>
            <ul>
              {r.items.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </article>
        ))}
      </div>
    </Modal>
  );
}