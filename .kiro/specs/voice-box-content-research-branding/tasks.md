# Implementation Plan: Voice Box·콘텐츠 연구·브랜딩

## Overview

기존 Python Lambda Function URL 백엔드, React(TypeScript) 프론트엔드, 단일 비공개 S3 저장소, 기존 인증·Bedrock Grant·스타일 학습 경계를 유지하면서 VOC 보관함, 활동 분석, 링크 기반 연구, 출처 표시, 브랜드 방향, 모바일 접근성을 작은 순서형 작업으로 추가한다. 각 구현 단계는 앞 단계의 계약과 저장 모델을 사용하고, 마지막 단계에서 기존 화면과 새 기능을 연결한다.

이 계획은 코드 작성·수정·자동화 테스트 범위만 포함한다. 사용자 승인 전에는 배포, Git push, IAM 정책 적용, 운영 리소스 변경을 수행하지 않는다.

## Tasks

- [x] 1. 기존 계약과 공통 기반 확장
  - [x] 1.1 기존 `handler.py`, `access_controller.py`, `s3store.py`, `errors.py`, `usage_meter.py`, 프론트엔드 `api.ts`와 탭 구조를 조사하고 새 action/응답 타입을 기존 패턴에 맞춰 정의한다.
    - 인증된 Session_Token의 검증 userId와 `user_scope.bind()`를 모든 신규 action의 기준으로 삼는다.
    - `userId`, `loginId`, S3 key가 요청 본문에 들어와도 소유권 판단에 사용하지 않는 공통 입력 계약을 추가한다.
    - _요구사항: 1.4, 10.2, 10.3, 13.1, 13.3_
  - [x] 1.2 공통 식별자·시간·페이지네이션·검증 타입과 오류 매핑을 추가한다.
    - `validation_error`, `forbidden`, `not_found`, `budget_exhausted`, `partial_result`를 기존 `{error:{code,message}}` 응답 규칙에 연결한다.
    - 신규 사용자 데이터 키는 상대 경로만 받아 `s3store.user_key()`로 생성하도록 인터페이스를 고정한다.
    - _요구사항: 1.1, 2.1, 4.3, 7.5, 10.1, 13.3_
  - [x]* 1.3 공통 action 디스패치와 사용자 범위·권한 오류에 대한 백엔드 계약 테스트를 작성한다.
    - 개인 action, 관리자 action, Bedrock Grant 필요 action의 401/403 응답을 검증한다.
    - _요구사항: 1.4, 2.3, 10.2, 10.3_

- [x] 2. VOC 저장과 관리자 상태 관리
  - [x] 2.1 `backend/voc_store.py`에 VOC_Record 모델, 입력 검증, 사용자별 생성·조회·수정·보관 기능을 구현한다.
    - `body`와 `userMetadata(sourceType, tags, note)`를 분리 저장하고 ownerId는 검증된 token userId로 채운다.
    - `voc-index.json`에는 목록용 최소 필드만 저장하고 상세 레코드는 사용자 prefix 아래 별도 JSON으로 저장한다.
    - _요구사항: 1.1, 1.2, 1.3, 1.5, 10.1, 13.3_
  - [x] 2.2 VOC 상태 전이와 관리자 필터 action을 구현한다.
    - 허용 상태를 `new`, `reviewing`, `confirmed`, `archived`로 제한한다.
    - `admin-list-voc`에 상태·작성 시각 범위·페이지네이션을 적용하고 `accessScope`를 반환한다.
    - 일반 사용자의 타 사용자 상태 변경은 저장하지 않고 관리자 권한 오류를 반환한다.
    - _요구사항: 2.1, 2.2, 2.3, 10.5_
  - [x] 2.3 VOC 생성·수정·보관·상태 변경을 `activity_store.py`의 Activity_Event 기록과 연결한다.
    - 상태 변경 이벤트에 actor, 이전 상태, 새 상태, 변경 시각을 포함한다.
    - 월별 activity JSON append/update에 optimistic concurrency를 적용한다.
    - _요구사항: 1.5, 2.4, 10.1_
  - [x]* 2.4 VOC 저장·상태 전이 property 테스트를 작성한다.
    - **Property 1: 사용자별 저장 격리**
    - **Validates: 요구사항 1.1, 1.2, 10.1, 13.3**
    - **Property 2: VOC 원문·메타데이터 보존**
    - **Validates: 요구사항 1.3**
    - **Property 3: 상태 값과 변경 이벤트 불변식**
    - **Validates: 요구사항 1.5, 2.1, 2.4**
  - [x]* 2.5 VOC action의 실제 JSON 요청·S3 mock·인증 컨텍스트를 사용하는 통합 테스트를 작성한다.
    - _요구사항: 1.1~1.5, 2.1~2.4, 10.1~10.2_

- [x] 3. Activity API와 시각화 상세 데이터
  - [x] 3.1 `backend/activity_store.py`에 기간·granularity별 activity 조회와 일별 집계 DTO를 구현한다.
    - acting user의 이벤트만 반환하고 `daily`, `events`, `range`를 제공한다.
    - 관리자 조회는 허용된 범위와 요청자를 audit metadata에 기록한다.
    - _요구사항: 3.1, 10.1, 10.5_
  - [x] 3.2 `get-activity` action을 handler에 연결하고 활동 유형·관련 VOC/Research_Run 식별자를 포함한 상세 모델을 반환한다.
    - 빈 기간은 오류가 아니라 빈 결과와 기간 변경 안내에 필요한 상태로 반환한다.
    - _요구사항: 3.2, 3.4, 10.1_
  - [x] 3.3 `ActivitySection.tsx`와 `ActivityDetail.tsx`를 구현한다.
    - 데스크톱 scatter/heatmap과 모바일 목록 대체 보기를 같은 event 모델로 구동한다.
    - 점과 셀은 날짜·활동 유형·활동 수가 포함된 accessible name을 제공한다.
    - _요구사항: 3.1, 3.2, 3.3, 3.5, 12.1, 12.5_
  - [x] 3.4 hover, focus, Enter/Space, click/touch 입력이 같은 상세 패널 모델을 열고 닫도록 연결한다.
    - 상세에는 날짜 또는 시간대, 활동 유형, 활동 수, 관련 VOC 또는 Research_Run 식별자를 표시한다.
    - 색상 외 텍스트 범례와 숫자 정보를 항상 노출한다.
    - _요구사항: 3.2, 3.3, 3.5, 12.4, 12.5_
  - [x]* 3.5 Activity API 집계·사용자 필터와 상세 입력 방식 property/컴포넌트 테스트를 작성한다.
    - **Property 13: 활동 상세의 입력 방식 불변성**
    - **Validates: 요구사항 3.2, 3.5, 12.4**
    - **Property 14: 활동 시각화의 사용자 격리와 접근 가능한 설명**
    - **Validates: 요구사항 3.1, 3.3**

- [x] 4. Research 링크 수집과 source 저장
  - [x] 4.1 `backend/research.py`에 URL 정규화와 SSRF 방어 검사를 구현한다.
    - http/https만 허용하고 localhost·private/link-local IP·비정상 포트를 차단한다.
    - fragment 제거, host lowercase, 기본 포트 제거 결과를 cache와 source 기록에 사용한다.
    - _요구사항: 4.2, 7.2, 10.4, 13.2_
  - [x] 4.2 `backend/research_store.py`에 Info_Source, Research_Run, Research_Summary, cache 저장 모델을 구현한다.
    - 사용자별 `research-runs`, `research-summaries`, `info-sources`, `research-cache` 키를 사용한다.
    - 성공·실패·skipped source 모두 상태, 정규화 URL, 수집 시각, 오류 원인을 보존한다.
    - _요구사항: 4.2, 4.4, 4.5, 5.4, 10.1, 13.3_
  - [x] 4.3 3~5개 링크 cardinality 검증과 기본 링크 수집 단계를 구현한다.
    - 링크가 3개 미만 또는 5개 초과이면 수집·Bedrock 호출 없이 입력 오류를 반환한다.
    - 링크별 접근 결과와 제한된 excerpt를 저장하고 하나의 실패가 다른 유효 링크 결과를 제거하지 않게 한다.
    - _요구사항: 4.1, 4.2, 4.3, 4.5, 7.4_
  - [x] 4.4 `run-research`, `get-research-run`, `list-research-runs` action을 handler와 Bedrock Grant 검사에 연결한다.
    - 브라우저가 직접 Bedrock 또는 S3를 호출하지 않고 기존 `postJson` 경계를 통과하게 한다.
    - _요구사항: 5.1, 10.3, 10.4, 13.1_
  - [x]* 4.5 링크 cardinality·정규화·source 기록·부분 수집 property 테스트를 작성한다.
    - **Property 4: 링크 집합 cardinality와 source 기록**
    - **Validates: 요구사항 4.1, 4.2, 4.3**
    - **Property 5: 부분 수집 보존**
    - **Validates: 요구사항 4.5, 5.5, 7.5**

- [x] 5. 검색 보완, budget, cache, partial 결과
  - [x] 5.1 명시적 검색 요청에만 Web_Search를 실행하는 provider 경계를 구현한다.
    - 검색 결과에 `sourceType=web-search`, query, 실행 시각, 결과 URL을 저장한다.
    - `maxSearchCalls`를 넘기기 전에 중단하고 검색 미요청 시 provider를 호출하지 않는다.
    - _요구사항: 5.1, 5.2, 5.3, 5.4_
  - [x] 5.2 Research budget evaluator와 Lambda 시간 guard를 구현한다.
    - requests, inputChars, outputTokens, searchCalls 각각의 상한을 단계 실행 전에 검사한다.
    - 예산 또는 실행 시간에 도달하면 추가 수집·검색·생성을 중단하고 partial 결과와 controls를 반환한다.
    - _요구사항: 7.1, 7.4, 7.5, 13.5_
  - [x] 5.3 정규화 링크 집합·요약 설정·budget profile 기반 cache key와 TTL/fingerprint 검사를 구현한다.
    - 동일한 유효 cache는 재사용하고 만료 또는 원본 fingerprint 변경 시 새 run과 만료 시각을 생성한다.
    - cache hit usage를 기록하고 기존 usage 필드와 새 research usage를 함께 유지한다.
    - _요구사항: 7.2, 7.3, 7.5, 13.5_
  - [x] 5.4 excerpt truncation과 요약 호출을 구현한다.
    - 원문 전체가 아닌 budget 내 필요한 excerpt만 untrusted data block으로 Bedrock에 전달한다.
    - 사실·생성·미확인 결과를 분리하고 Grant가 없으면 수집 결과를 임의 생성하지 않는다.
    - _요구사항: 4.4, 5.5, 7.4, 9.5, 10.3_
  - [ ]* 5.5 검색 명시성·budget·cache·부분 결과 property 테스트를 작성한다.
    - **Property 6: 검색 명시성과 횟수 상한**
    - **Validates: 요구사항 5.1, 5.2, 5.3, 5.4**
    - **Property 7: 예산 내 실행과 조기 중단**
    - **Validates: 요구사항 7.1, 7.5, 13.5**
    - **Property 8: 캐시 정규화와 유효성**
    - **Validates: 요구사항 7.2, 7.3**

- [x] 6. Research 화면과 요약 출처 표시
  - [x] 6.1 `ResearchView.tsx`에 링크 3~5개 입력, 명시적 검색 보완 옵션, 예산 표시, 실행 상태, partial 결과 UI를 구현한다.
    - 입력 오류와 budget exhausted/partial 상태를 명확한 텍스트로 표시한다.
    - _요구사항: 4.1, 4.3, 5.1, 5.3, 7.1, 7.5, 12.2_
  - [x] 6.2 `SourceLabel.tsx`와 `ResearchSummary.tsx`를 구현한다.
    - `사실`, `생성`, `사용자 입력`, `출처 미확인`을 색상만이 아닌 텍스트 배지와 설명으로 구분한다.
    - 사실 콘텐츠에만 source 제목/도메인과 안전한 external link를 제공한다.
    - _요구사항: 9.1, 9.2, 9.3, 9.5, 12.5_
  - [ ]* 6.3 Research 화면·요약·SourceLabel contract 및 컴포넌트 테스트를 작성한다.
    - **Property 12: 출처 표시 안전성**
    - **Validates: 요구사항 9.1, 9.2, 9.3, 9.5**

- [x] 7. Style/Research 분리와 draft provenance
  - [x] 7.1 `backend/drafting_engine.py`에 연구 자료 조회와 `RESEARCH_CONTEXT`/`STYLE_CONTEXT` 분리 입력을 구현한다.
    - Info_Source와 VOC는 정보 영역에만 넣고 Style_Profile·Post_Chunk는 스타일 영역에만 넣는다.
    - 연구 자료를 Style_Profile, Vector_Index, Post_Chunk, raw-posts에 저장하지 않는다.
    - _요구사항: 6.1, 6.2, 6.3, 6.4, 8.3_
  - [x] 7.2 스타일 retrieval의 Top_K·context token cap과 일반 문체 fallback을 연결한다.
    - 유효 Style_Profile 또는 retrieval 결과가 없으면 `generalStyle=true`를 반환한다.
    - fallback에서도 Info_Source·VOC는 정보 입력으로만 사용한다.
    - _요구사항: 8.1, 8.2, 8.3, 8.4_
  - [x] 7.3 `generate-draft`, `get-result`에 researchRunId/infoSourceIds/vocIds와 provenance 저장·조회 계약을 추가한다.
    - draft에 사용 source 식별자, fact/generated/unverified segment metadata, research 상태를 저장한다.
    - 기존 이미지 업로드, imageKeys, GrantSelect, Markdown 저장 계약은 유지한다.
    - _요구사항: 6.3, 9.4, 13.4_
  - [ ]* 7.4 drafting_engine mock 테스트와 분리·fallback·retrieval property 테스트를 작성한다.
    - **Property 9: 연구 자료와 스타일 자료 분리**
    - **Validates: 요구사항 6.1, 6.2, 6.3, 6.4, 8.3**
    - **Property 10: 일반 문체 fallback**
    - **Validates: 요구사항 8.1, 8.2, 8.3**
    - **Property 11: 제한된 retrieval과 prompt 입력**
    - **Validates: 요구사항 7.4, 8.4**

- [x] 8. VOC·Research 프론트엔드 화면 연결
  - [x] 8.1 `VocEditor.tsx`, `VocList.tsx`, `VoiceBoxView.tsx`를 구현하고 create/list/get/update/archive action을 연결한다.
    - 상태·기간·검색 필터와 모바일 카드 목록을 제공한다.
    - 원문·sourceType·tags·note 입력과 validation 오류를 분리 표시한다.
    - _요구사항: 1.1, 1.2, 1.3, 1.5, 2.1, 12.1, 12.2_
  - [x] 8.2 `App.tsx` 또는 기존 마이페이지 흐름에 Voice Box, Research, Activity 화면을 연결한다.
    - 기존 기본 style 탭과 기존 화면 흐름은 유지한다.
    - _요구사항: 3.1, 4.1, 6.3, 13.4_
  - [x] 8.3 `DraftingView.tsx`와 `ResultView.tsx`에 연구 선택, provenance, SourceLabel 렌더링을 연결한다.
    - 기존 초안 생성·이미지·학습 포스트 조회/제목 변경 흐름과 연구 결과를 다른 데이터 모델로 유지한다.
    - _요구사항: 6.3, 9.1, 9.4, 13.4_
  - [ ]* 8.4 VOC/Research/Draft 프론트엔드 action contract와 사용자 흐름 테스트를 작성한다.
    - _요구사항: 1.1~1.5, 4.1~5.5, 8.1~9.5_

- [x] 9. 브랜드 자산, 디자인 토큰, 적용 규칙
  - [x] 9.1 펭귄 색연필 참조를 복제하지 않고 브랜드 토큰과 손그림 질감 fallback을 프론트엔드 스타일 시스템에 추가한다.
    - `--brand-ink`, `--brand-green`, `--brand-green-dark`, `--brand-paper`, `--brand-pencil-yellow`, `--brand-pencil-coral`, `--brand-pencil-blue`, `--brand-line`을 정의한다.
    - Pretendard, 따뜻한 중립색, 단일 녹색 primary, 무그라데이션, lucide-react 아이콘 원칙을 기존 Calm_Design_Guide와 함께 적용한다.
    - _요구사항: 11.1, 11.2, 11.4, 13.1_
  - [x] 9.2 빈 상태·section accent·브랜드 영역에만 캐릭터/연필 분위기를 적용하고 출처·본문·오류·상태 정보의 대비를 우선하는 컴포넌트 스타일을 구현한다.
    - 브랜드 적용 규칙을 토큰과 컴포넌트 API의 사용 제약으로 반영해 장식이 정보 계층을 가리지 않게 한다.
    - _요구사항: 11.2, 11.3, 11.4, 9.1, 12.3_
  - [ ]* 9.3 디자인 토큰의 대비·출처 라벨·브랜드 fallback 스타일 회귀 테스트 또는 정적 검사를 추가한다.
    - _요구사항: 9.1, 9.2, 11.1~11.4, 12.3_

- [x] 10. 모바일 반응형과 접근성 보강
  - [x] 10.1 320px 이상 화면에서 VOC 입력, 링크 목록, 출처 목록, 요약, 상태 조작을 세로 흐름 또는 카드 레이아웃으로 재배치한다.
    - 가로 잘림을 만들지 않고 activity 차트에는 가로 스크롤과 목록 대체 보기를 함께 제공한다.
    - _요구사항: 3.5, 12.1, 12.2_
  - [x] 10.2 모든 입력·오류·진행·시각화 요소에 label, `aria-describedby`, `role=alert`, `role=status`, 포커스 순서와 텍스트 대체 설명을 연결한다.
    - 200% 확대와 hover 불가 터치 환경에서도 SourceLabel, VOC_Status, 범례, 상세 패널 의미를 보존한다.
    - _요구사항: 3.2, 3.3, 5.3, 9.1, 12.3, 12.4, 12.5_
  - [ ]* 10.3 320px·375px·데스크톱 viewport, keyboard/touch interaction, axe 기반 접근성 및 overflow 테스트를 작성한다.
    - **Property 15: 반응형 핵심 기능 보존**
    - **Validates: 요구사항 3.4, 12.1, 12.2, 12.3, 12.5**

- [x] 11. 회귀·통합 테스트와 infra 정적 검사
  - [x] 11.1 기존 backend auth, grant, style learner, draft history, usage meter 테스트와 기존 frontend App, StyleLearningView, DraftingView, ResultView, UsageSection 테스트를 유지하고 신규 계약 변경에 맞게 최소 수정한다.
    - 기존 수용 기준과 저장 경계를 변경하지 않았음을 검증한다.
    - _요구사항: 6.5, 10.3, 13.4_
  - [x] 11.2 handler 통합 테스트에 신규 action의 인증, 권한, validation, partial, budget exhausted, cache hit/miss 응답을 추가한다.
    - mock S3/auth/Bedrock provider를 사용하고 실제 외부 배포나 네트워크 호출은 하지 않는다.
    - _요구사항: 1.4, 2.3, 4.3, 5.5, 7.5, 10.1~10.4, 13.5_
  - [x] 11.3 S3 key·private/SSE·최소 권한·브라우저 직접 Bedrock 호출 금지·제외 서비스에 대한 infra 정적 검사와 테스트 fixture를 추가한다.
    - IAM 정책을 적용하거나 운영 AWS 리소스를 변경하지 않고 검사 코드와 문서화된 검증 기준만 추가한다.
    - _요구사항: 10.4, 13.2, 13.3_
  - [ ]* 11.4 전체 Python/React 테스트와 정적 검사를 단일 로컬 검증 명령으로 실행할 수 있도록 테스트 스크립트를 정리한다.
    - 배포, push, IAM 적용 없이 회귀 테스트 결과를 확인한다.
    - _요구사항: 6.5, 10.4, 13.1~13.5_

- [x] 12. 최종 연결 및 검증 체크포인트
  - [x] 12.1 모든 신규 화면과 action을 기존 인증·S3·Bedrock Grant·usage/activity 경계에 연결하고 orphan endpoint/component가 없는지 확인한다.
    - _요구사항: 1.1~13.5_
  - [x] 12.2 구현 범위가 사용자 승인 전 배포·push·IAM 적용을 하지 않는 상태인지 확인하고, 전체 테스트가 통과하는지 확인한다.
    - _요구사항: 10.4, 13.1~13.5_

## 후속 작업 (사용자 피드백)

- [x] 13. 매뉴얼·업데이트 노트 정정
  - [x] 13.1 dialogs.test.tsx의 매뉴얼 이미지 기대값을 9장(신규 단계 포함)으로 갱신하고, 업데이트 노트 날짜를 실제 배포일로 맞춘 뒤 vitest를 통과시킨다.
    - _요구사항: 사용자 매뉴얼 갱신(추가 요청)_
- [x] 14. 계정 메뉴(아이디 버튼) 복구
  - [x] 14.1 하단 아이디 버튼을 누르면 마이페이지·로그아웃 메뉴가 열리도록 원인을 찾아 고치고, 메뉴 열기·닫기·로그아웃 동작 테스트를 추가한다.
    - _요구사항: 계정 메뉴 회귀 수정(추가 요청)_
- [x] 15. 토큰 등급별 초안 모델 정책
  - [x] 15.1 backend: AI 서비스 토큰(Grant)이 없는 회원도 generate-draft를 호출할 수 있게 하되, 응답에 tier(quality 또는 lite)를 넣는다. Grant 승인자는 quality(MABLOP_TEXT_MODEL), 미보유자는 lite 모델(MABLOP_LITE_MODEL)을 사용한다. 학습(learn-style)과 연구 실행(run-research)은 기존처럼 Grant를 요구한다.
    - _요구사항: 토큰 미보유자 정책(추가 요청)_
  - [x] 15.2 backend: lite 등급은 회원별 월 호출 한도(MABLOP_LITE_MONTHLY_LIMIT, 기본 5회)를 usage_meter의 월 문서에 기록해 넘기면 quota_exceeded(429)로 막는다. 한도 초과 시 안내 메시지를 반환한다.
    - _요구사항: 비용 상한(추가 요청)_
  - [x] 15.3 frontend: DraftingView에서 Grant가 없으면 "저품질 초안(월 N회)" 안내와 남은 횟수를 표시하고, 응답 tier에 따라 결과 화면에 등급을 표시한다. Grant 선택 UI와 API 계약(api.ts)을 맞춘다.
    - _요구사항: 토큰 미보유자 정책(추가 요청)_
  - [x] 15.4 infra 확인만: lite 모델을 호출하려면 Lambda 역할 정책에 해당 모델 ARN 권한이 필요하다. 템플릿과 IAM 정책은 변경하지 말고, 필요한 ARN과 적용 명령을 보고서에만 적는다. 적용은 사용자 승인 후 별도로 한다.
    - _요구사항: IAM 변경 승인 절차(추가 요청)_
## 후속 작업 3차 (사용자 피드백)

- [x] 16. 사용자의 목소리함을 운영자용 문의 게시판으로 재설계
  - [x] 16.1 백엔드: VOC 레코드에 답변(reply) 필드를 추가하고, mablop_admin(운영자·개발자)만 답변을 달 수 있는 admin-reply action을 만든다. 회원 조회 응답에는 답변을 포함한다. 관리자 계정의 create-voc는 거부한다. 일반 회원만 의견을 등록할 수 있다.
    - _요구사항: 목소리함은 운영자에게 가는 문의 게시판(추가 요청)_
  - [x] 16.2 프론트 목록: https://agof.co.kr/board/product/list.html?board_no=6 과 faq-list.html 의 목록 디자인(번호·제목·작성자·날짜·상태 행, 모바일은 행 카드)을 참고해 구현한다. 답변이 달린 글에는 "Re:" 표시를 한다. 복제하지 말고 구조만 참고한다.
    - _요구사항: 목록 보기 디자인(추가 요청)_
  - [x] 16.3 프론트 작성: https://agof.co.kr/board/product/write.html?board_no=6 의 글쓰기 구성(분류 select, 제목, 본문, 등록 버튼)을 참고한다. 입력 항목은 분류·제목·본문으로 줄이고 태그·메모 필드는 제거한다. 관리자 화면에서는 작성 폼을 숨기고 목록에서 답변 작성만 보인다.
    - _요구사항: 의견 작성 스타일(추가 요청)_
- [x] 17. 사용자 매뉴얼의 목소리함 설명을 문의·건의 예시로 수정한다.
  - [x] 17.1 MANUAL_STEPS의 목소리함 단계 본문과 포인트를 "운영자·개발자에게 서비스 의견이나 문의를 남기는 곳"으로 고치고, 예시로 "6개월 전 초안 결과가 보이지 않아요"와 "글 재료 수집을 6개 이상 링크에서 가져올 수 있었으면 좋겠어요"를 넣는다. 목소리함 화면 캡처 09-voicebox.png를 새 UI로 다시 찍는다.
    - _요구사항: 사용자 매뉴얼 정정(추가 요청)_
- [x] 18. 헤더 태그라인과 배경 그라데이션을 직전 배포 버전(f277c5c) 디자인으로 되돌린다.
  - [x] 18.1 git 이력에서 f277c5c 시점의 App.tsx 헤더 마크업과 index.css 배경 규칙을 확인해, 태그라인 "쌓일수록 나다워지는 글쓰기 AI"와 배경 그라데이션을 복원한다. 그 뒤 추가된 다른 기능은 유지한다.
    - _요구사항: 디자인 원복(추가 요청)_
- [x] 19. 스타일 학습 탭에 보안 안내를 추가하고 설명문 줄바꿈을 정리한다.
  - [x] 19.1 보안 안내 문구를 넣는다. 내용은 실제 구현과 맞아야 한다: 학습 데이터는 본인 전용 저장소 공간에만 저장되고, 다른 사용자와 공유하지 않으며, 외부로 전송되지 않는다는 점. 과장된 표현(완벽 보안 등)은 쓰지 않는다.
  - [x] 19.2 설명문의 불필요한 줄바꿈을 제거한다. "학습한 글이 쌓일수록 초안이 내 말투에 가까워져요."가 한 줄로 보이게 한다.
    - _요구사항: 스타일 학습 안내 개선(추가 요청)_
- [x] 20. 첫 화면 기본 탭을 글 재료 수집으로 바꾼다.
  - [x] 20.1 App.tsx의 초기 view를 "research"로 바꾸고 테스트를 갱신한다.
    - _요구사항: 첫 화면 변경(추가 요청)_
- [x] 21. 글 재료 수집 화면을 고도화한다.
  - [x] 21.1 네이버 블로그 링크는 수집이 막히므로 입력 안내에 명시하고, 검증 단계에서 naver.com 도메인을 안내 메시지와 함께 거부한다.
  - [x] 21.2 웹 검색 보완을 실제로 동작하게 한다. 검색 공급자와 요약 모델은 사용자 결정 후 연결한다. 검색 결과는 링크와 AI가 정리한 요약으로 저장하고 출처를 표시한다. ChatGPT 답변을 그대로 복사하지 않는다.
  - [x] 21.3 입력 칸, 사용량 카드, 출처 카드, 결과 카드의 크기와 간격을 하나의 가로 그리드로 통일한다. 세로로 긴 카드를 줄인다.
  - [x] 21.4 "이 재료로 초안 쓰기" 버튼을 결과 영역 맨 아래 고정 위치에 둔다. 수집 버튼을 눌러도 위치가 바뀌지 않게 한다.
  - [x] 21.5 요약이 비는 원인을 고친다. 현재 "수집한 출처 3건 성공"인데 요약이 "no collected sources to summarize"로 나온다. 성공한 출처의 excerpt가 요약 단계에 전달되지 않는 것으로 보인다. 확인 후 고친다.
  - [x] 21.6 "최근 실행"은 날짜만 보이지 않게, 수집 요약 텍스트와 출처 목록을 보여준다. "이 재료로 초안 쓰기"를 누르면 초안 작성 탭의 글 재료 란에 수집일(날짜형)과 요약 텍스트가 그대로 표시되고, 초안 생성 요청에 그 run이 연결된다.
    - _요구사항: 글 재료 수집 UX 개선(추가 요청)_
- [x] 22. 저비용 고품질 모델 적용 (사용자 결정 후 진행)
  - [x] 22.1 결정된 API로 lite 등급 모델을 바꾸고, 가격 표(usage_meter PRICES)와 IAM 권한을 맞춘다. IAM 변경은 승인 후 배포 파이프라인으로만 적용한다.
    - _요구사항: 토큰 미보유자 초안 품질 개선(추가 요청)_
## 후속 작업 4차 (사용자 요구)

- [ ] 23. 구글 AI 개요 자동 수집과 초안 재료 붙여넣기
  - [ ] 23.1 백엔드: 키워드 검색 시 SerpApi(engine google, Google AI Overview)로 구글 AI 개요를 자동 수집해 "google-ai-overview" 출처로 저장한다. 키는 SSM /mablop/serp-api-key(env MABLOP_SERP_KEY_SSM_PATH)에서 읽고 로그·응답·예외 메시지에 남기지 않는다. 개요가 없는 검색어는 controls에 "ai_overview_unavailable"을 넣는다. 키가 없으면 "serp_provider_unavailable"을 넣고 건너뛴다. 검색 1회당 SerpApi 호출은 최대 2회 이내로 제한한다.
    - _요구사항: 구글 AI 개요 자동 수집(추가 요청)_
  - [ ] 23.2 백엔드: generate-draft가 materialText(사용자가 붙여넣은 재료, 최대 8000자)를 받아 정보 영역(RESEARCH_CONTEXT)에 user-input으로 넣는다. 스타일 영역에는 넣지 않는다.
    - _요구사항: 재료 붙여넣기(추가 요청)_
  - [ ] 23.3 프론트: 글 재료 결과 카드에 구글 AI 개요 본문을 보여주고 "복사" 버튼을 둔다. 초안 작성 화면의 글 재료 영역에 "붙여넣을 재료" 입력칸을 두고, 여기에 붙여넣은 내용을 generate-draft의 materialText로 보낸다. 개요가 없으면 "구글 AI 개요가 없는 검색어예요"를 표시한다.
    - _요구사항: 재료 복사·붙여넣기 UI(추가 요청)_
  - [ ] 23.4 인프라: infra/template.yaml과 lambda-role-policy.json에 /mablop/serp-api-key SSM 읽기 권한(ReadToken에 ARN 추가, !Sub 사용)과 Lambda env MABLOP_SERP_KEY_SSM_PATH를 추가한다. infra/tests의 ReadToken 개수 단정을 갱신한다. sam validate를 실행한다.
    - _요구사항: 배포 파이프라인으로 적용되는 IAM 변경(승인됨)_
## 체크포인트

- 2단계 완료 후 VOC 저장·상태 전이·권한 테스트가 통과해야 한다.
- 5단계 완료 후 링크 cardinality, 검색 미요청, budget 중단, cache 재사용, partial 결과 테스트가 통과해야 한다.
- 7단계 완료 후 style/research 분리, fallback, draft provenance 테스트가 통과해야 한다.
- 10단계 완료 후 모바일 overflow, 키보드·터치 상세, 출처 라벨 접근성 검사가 통과해야 한다.
- 12단계 완료 후 전체 테스트를 통과시키되 사용자 승인 전 배포·push·IAM 적용은 하지 않는다.

## Notes

- `*`가 붙은 하위 작업은 선택적 테스트 작업이다. 구현 작업은 선택 사항이 아니다.
- 모든 신규 데이터는 검증된 acting user의 `users/{userId}/` prefix 아래에만 저장한다.
- 기존 `raw-posts/`, `embeddings/`, `vector-index/`, `style-profile/`, `images/`, `generated-posts/`, `prompt-templates/`, `metadata/` 저장 원칙과 기존 인증·스타일 학습·초안·이미지 기능을 유지한다.
- 테스트는 mock provider와 로컬 fixture를 사용하며 외부 배포, Git push, IAM 정책 적용, 운영 변경은 이 계획의 범위 밖이다.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "9.1"] },
    { "id": 1, "tasks": ["1.2", "2.1", "3.1", "4.1", "9.2"] },
    { "id": 2, "tasks": ["1.3", "2.2", "2.3", "3.2", "4.2", "4.3", "10.1"] },
    { "id": 3, "tasks": ["2.4", "2.5", "3.3", "3.4", "4.4", "5.1", "5.2", "10.2"] },
    { "id": 4, "tasks": ["3.5", "4.5", "5.3", "5.4", "6.1", "6.2", "7.1", "7.2", "7.3", "8.1"] },
    { "id": 5, "tasks": ["5.5", "6.3", "7.4", "8.2", "8.3", "9.3", "10.3"] },
    { "id": 6, "tasks": ["8.4", "11.1", "11.2", "11.3"] },
    { "id": 7, "tasks": ["11.4", "12.1", "12.2"] }
  ]
}
```
