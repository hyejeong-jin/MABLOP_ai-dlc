// "업데이트 노트": release history, newest first. Opened with the bell button.
import { Modal } from "./Modal";

interface Release {
  version: string;
  date: string;
  title: string;
  tag?: string;
  items: string[];
}

export const RELEASES: Release[] = [
  {
    version: "v1.2.0",
    date: "2026.11.14",
    title: "글 재료 수집과 사용자의 목소리함",
    items: [
      "'글 재료 수집' 탭: 키워드 웹 검색으로 글 재료를 정리해요. 참고 링크는 선택(최대 5개)이고, 네이버 블로그 링크는 제외돼요.",
      "첫 화면이 '글 재료 수집'으로 바뀌었어요.",
      "'사용자의 목소리함' 탭: 운영자에게 의견·문의를 남기고 답변(Re:)을 받는 게시판이에요.",
      "초안 작성에서 글 재료를 고르면 미리보기를 볼 수 있어요. 말투는 내 글에서, 정보는 글 재료에서 가져와요.",
      "AI 서비스 토큰이 없어도 저품질 초안을 월 5회까지 쓸 수 있어요.",
      "스타일 학습에 보안 안내를 추가했어요. 내 글은 회원별 비공개 저장 공간에 저장돼요.",
      "포스트 작성 활동을 최근 1년 달력으로 보여줘요. 날짜 칸에 마우스를 올리면 초안·학습 횟수가 보여요.",
      "펭귄과 색연필 분위기의 차분한 브랜드 색을 적용하고, 모바일 화면을 다듬었어요.",
    ],
  },
  {
    version: "v1.1.0",
    date: "2026.11.10",
    title: "더 쉽고 보기 좋은 MABLOP",
    items: [
      "종 모양 버튼에서 업데이트 노트를 볼 수 있어요.",
      "사용자 매뉴얼에 화면 예시를 넣었고, 로그인하면 안내가 먼저 열려요. '하루 동안 보지 않기'도 고를 수 있어요.",
      "학습 완료 목록의 글을 눌러 내용을 보고, 포스트 제목(ID)을 바꿀 수 있어요.",
      "로그인 화면에 '비밀번호를 잊으셨나요?'와 Caps Lock 켜짐·꺼짐 표시를 추가했어요.",
      "모바일에서 화면 폭과 상단 버튼 정렬이 어긋나던 문제를 바로잡았어요.",
      "포스트 작성 활동 표를 초안 작성과 글 학습 기준으로 고치고 한국 시간에 맞췄어요. 누적 토큰은 최근 6개월 합계로 바꿨어요.",
      "'AI 서비스 토큰 사용 권한' 등 문구를 알기 쉽게 다듬고, 화면 디자인을 정돈했어요.",
      "하단에 연락처(contact)를 추가했어요.",
    ],
  },
  {
    version: "v1.0.0",
    date: "2026.11.07",
    title: "MABLOP 서비스 오픈",
    tag: "첫 릴리스",
    items: [
      "내 블로그 문체를 학습해 초안을 써 주는 글쓰기 AI, MABLOP을 처음 공개했어요.",
      "로그인과 가입 신청, 관리자 승인, 계정 관리(비활성화·비밀번호 초기화·삭제)",
      "사용자별로 나뉜 학습 데이터와 초안, AI 서비스 토큰 사용 권한 신청과 승인",
      "스타일 학습: 네이버·티스토리·벨로그의 공개 글 또는 붙여넣기",
      "초안 작성: 이미지 삽입과 일반 문체 초안, 결과 탭에서 지난 초안 보기",
      "마이페이지: 월 사용량, 누적 토큰, 포스트 작성 활동",
      "다크 모드와 일반 모드, 사용자 매뉴얼",
    ],
  },
];

export const LATEST_VERSION = RELEASES[0].version;

export function UpdateNotesDialog({ onClose }: { onClose: () => void }) {
  return (
    <Modal title="업데이트 노트" onClose={onClose}>
      <p className="meta">새로운 기능과 개선사항을 확인하세요. 오른쪽 위 종 모양 버튼에서 언제든 다시 볼 수 있어요.</p>
      <div className="release-list">
        {RELEASES.map((r) => (
          <article className="release" key={r.version}>
            <div className="release-head">
              <span className="badge badge-active">{r.version}</span>
              {r.tag && <span className="badge badge-pending">{r.tag}</span>}
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