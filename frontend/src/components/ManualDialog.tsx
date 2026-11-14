// "사용자 매뉴얼": step-by-step guide with annotated screen examples.
// Opened with the "?" button, and once after login unless the user chose "하루 동안 보지 않기".
import { useState } from "react";

import { asset } from "../config";
import { Modal } from "./Modal";

interface Step {
  title: string;
  image: string;
  alt: string;
  body: string;
  points: string[];
}

export const MANUAL_STEPS: Step[] = [
  {
    title: "1. 가입하고 로그인하기",
    image: "manual/01-login.png",
    alt: "로그인 화면. 가입 신청 탭, 아이디와 비밀번호 입력칸, 로그인 버튼",
    body: "처음이라면 '가입 신청' 탭에서 신청하세요. 관리자가 승인하면 같은 화면에서 로그인할 수 있어요.",
    points: ["① 로그인과 가입 신청을 오가는 탭", "② 아이디와 비밀번호 (비밀번호는 10자 이상)", "③ 비밀번호를 잊었다면 '비밀번호를 잊으셨나요?'"],
  },
  {
    title: "2. AI 서비스 토큰 사용 권한 받기",
    image: "manual/02-grant.png",
    alt: "마이페이지의 AI 서비스 토큰 사용 권한 신청 영역",
    body: "글을 학습하거나 초안을 쓰거나 글 재료를 모으려면 사용 권한이 필요해요. 마이페이지에서 신청하고, 관리자가 승인하면 사용할 수 있어요. 권한이 없을 시 관리자에게 요청하세요.",
    points: ["① 사용 목적 (선택 입력)", "② 사용 권한 신청 버튼", "③ 승인된 권한과 만료일. 학습·글 재료·초안 화면의 목록에서 선택해요"],
  },
  {
    title: "3. 내 블로그 글 학습시키기 (말투 학습)",
    image: "manual/03-learn.png",
    alt: "스타일 학습 화면. 블로그 URL 입력, 포스트 수, 미리보기와 학습 시작 버튼",
    body: "네이버, 티스토리, 벨로그의 공개 글을 읽어 내 문체를 익혀요. 여기에 넣는 링크는 말투 학습용이에요. 학습이 끝난 글은 목록에서 눌러 내용을 보고 제목도 바꿀 수 있어요.",
    points: ["① 사용 권한 선택", "② AI에게 학습시킬 나의 블로그 URL", "③ 공개 포스트 미리보기 후 학습 시작", "④ 학습 완료 목록의 글을 누르면 내용 확인과 제목 변경"],
  },
  {
    title: "4. 글 재료 모으기 (정보 수집)",
    image: "manual/08-research.png",
    alt: "글 재료 수집 화면. 정보 수집 링크 입력칸 3개, 링크 추가, 웹 검색 보완 체크박스, 자료 수집하기 버튼",
    body: "쓰고 싶은 주제의 참고 링크를 3~5개 붙여 넣으면 사실 근거를 모아 요약해요. 이 링크는 말투 학습에 쓰이지 않아요. 같은 링크를 다시 넣으면 이전 결과를 재사용해 추가 비용이 들지 않아요.",
    points: ["① 사용 권한 선택", "② 정보 수집 링크 3~5개 (링크 추가로 최대 5개)", "③ 웹 검색 보완 (선택, 기본 꺼짐. 켜면 검색 1회당 추가 비용)", "④ 자료 수집하기. 결과는 '사실·생성·사용자 입력·출처 미확인' 라벨로 구분돼요"],
  },
  {
    title: "5. 초안 쓰기",
    image: "manual/04-draft.png",
    alt: "초안 작성 화면. 제목, 개요, 메모 입력칸, 글 재료 선택, 초안 생성 버튼",
    body: "제목, 개요, 메모를 적고 초안 생성을 누르세요. 이미지는 최대 10장까지 첨부할 수 있어요. '글 재료 선택하기'로 모아 둔 글 재료나 목소리함 의견을 함께 넣을 수 있어요. 말투는 학습한 내 글에서, 정보는 글 재료에서 가져와요.",
    points: ["① 제목", "② 개요: 어떤 내용을 쓸지 개괄적으로", "③ 메모: 방향성, 말투, AI에게 요청할 내용", "④ 글 재료 (선택): 수집한 재료와 목소리함 의견", "⑤ 초안 생성"],
  },
  {
    title: "6. 결과 확인하기",
    image: "manual/05-result.png",
    alt: "결과 화면. 왼쪽에 초안 목록, 오른쪽에 선택한 초안 본문",
    body: "만든 초안은 모두 '결과' 탭에 쌓여요. 왼쪽 목록에서 고르면 오른쪽에서 읽고, 복사하거나 지울 수 있어요. 글 재료를 썼다면 '사용한 글 재료'에서 출처와 라벨을 확인할 수 있어요.",
    points: ["① 지난 초안 목록", "② 선택한 초안 본문", "③ 복사·다운로드·삭제"],
  },
  {
    title: "7. 사용자의 목소리함",
    image: "manual/09-voicebox.png",
    alt: "사용자의 목소리함 화면. 원문, 출처 유형, 태그, 메모 입력칸과 의견 목록",
    body: "독자나 고객의 말, 떠오른 아이디어를 모아 두는 나만의 보관함이에요. 다른 사람에게는 보이지 않아요. 모아 둔 의견은 초안을 쓸 때 정보로 넣을 수 있어요.",
    points: ["① 원문: 들은 말이나 반응을 그대로", "② 출처 유형·태그·메모", "③ 의견 저장", "④ 상태·검색·기간으로 찾고 수정·보관"],
  },
  {
    title: "8. 사용량과 활동 보기",
    image: "manual/06-usage.png",
    alt: "마이페이지의 내 월 사용량과 포스트 작성 활동",
    body: "마이페이지에서 이번 달 비용, 모델별 사용량, 누적 토큰을 볼 수 있어요. 포스트 작성 활동에서 색깔 박스에 마우스를 올리거나 누르면 그날의 날짜와 초안·학습 횟수가 보여요.",
    points: ["① 이번 달 예상 비용과 누적 토큰", "② 모델별 비용 표", "③ 포스트 작성 활동: 진할수록 활동이 많은 날", "④ 박스 위에 올리면 날짜, 초안·학습 횟수"],
  },
  {
    title: "9. 화면 위쪽 도구",
    image: "manual/07-header.png",
    alt: "화면 오른쪽 위의 종, 달, 물음표 버튼",
    body: "오른쪽 위 버튼으로 새 소식과 화면 모드, 이 안내를 열 수 있어요. 내 아이디는 왼쪽 아래에 있고, 누르면 마이페이지와 로그아웃이 나와요.",
    points: ["① 종: 업데이트 노트", "② 달·해: 다크 모드와 일반 모드", "③ 물음표: 사용자 매뉴얼 (지금 보는 안내)"],
  },
];

interface Props {
  onClose: () => void;
  /** First-view mode after login: shows the "하루 동안 보지 않기" checkbox. */
  welcome?: boolean;
  onHideToday?: () => void;
}

export function ManualDialog({ onClose, welcome = false, onHideToday }: Props) {
  const [hideToday, setHideToday] = useState(false);

  function close() {
    if (welcome && hideToday) onHideToday?.();
    onClose();
  }

  const footer = (
    <>
      {welcome ? (
        <label className="check-line">
          <input type="checkbox" checked={hideToday} onChange={(e) => setHideToday(e.target.checked)} />
          하루 동안 보지 않기
        </label>
      ) : (
        <span />
      )}
      <button type="button" className="btn-primary" onClick={close}>
        {welcome ? "확인했어요" : "닫기"}
      </button>
    </>
  );

  return (
    <Modal title="사용자 매뉴얼" onClose={close} footer={footer} wide>
      {welcome && <p className="meta manual-lead">처음 오셨나요? 아래 순서대로 따라 하면 첫 초안까지 만들 수 있어요.</p>}
      <ol className="manual-list">
        {MANUAL_STEPS.map((step) => (
          <li key={step.title}>
            <h3>{step.title}</h3>
            <p>{step.body}</p>
            <figure className="manual-figure">
              <img src={asset(step.image)} alt={step.alt} loading="lazy" />
            </figure>
            <ul className="manual-points">
              {step.points.map((point) => (
                <li key={point}>{point}</li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
      <p className="meta">문의는 개발자 hyejeong.jin에게 해 주세요.</p>
    </Modal>
  );
}