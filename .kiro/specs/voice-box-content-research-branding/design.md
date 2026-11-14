# 설계 문서: Voice Box·콘텐츠 연구·브랜딩

## Overview

이 기능은 기존 MABLOP의 React SPA, Python Lambda Function URL, 단일 비공개 S3 버킷, Bedrock 호출 경계를 확장한다. 기존 인증과 사용자별 저장 경로를 재사용하며, 새 기능의 모든 사용자 데이터는 `users/{verifiedUserId}/` 아래에 둔다. 브라우저는 `api.ts`를 통해 Lambda만 호출하고, Bedrock과 S3 JSON 데이터에는 직접 접근하지 않는다. 이미지 바이트 업로드만 기존 presigned PUT 경로를 사용한다.

기존 스타일 학습 데이터와 새 콘텐츠 연구 데이터는 저장 영역과 프롬프트 입력 영역을 분리한다. VOC와 연구 자료는 스타일 학습에 자동 편입되지 않는다. 연구 실행은 링크 수, 입력량, 출력량, 검색 횟수를 예산으로 제한하며, 일부 링크 실패나 예산 소진 시 이미 확보한 결과를 `partial` 상태로 반환한다.

## Architecture

### 2.1 백엔드 계층

- `backend/handler.py`: 기존 `action` 기반 단일 디스패처에 새 action을 추가한다. `access_controller.check_request()`와 `check_grant()` 이후 `user_scope.bind(ctx.user_id)` 안에서 실행한다.
- `backend/access_controller.py`: 기존 Session_Token 검증, 역할별 관리자 action, Bedrock Grant 검사를 유지한다. VOC 개인 action은 로그인 사용자만, 관리자 VOC 목록·상태 변경 action은 `ADMIN_ONLY_ACTIONS`에 추가한다. 연구 실행과 연구 자료를 포함한 초안 생성은 기존 `BEDROCK_ACTIONS`와 Grant 규칙을 따른다.
- `backend/s3store.py`: `user_key()`만으로 사용자 키를 만들며 요청 본문의 `userId`, `loginId`, S3 key를 소유권 판단에 사용하지 않는다. 새 모듈은 상대 경로만 전달하고 사용자 prefix를 직접 조립하지 않는다.
- `backend/voc_store.py`(신규): VOC 레코드, 사용자 목록, 수정·보관, 관리자 상태 전환과 activity 기록을 담당한다.
- `backend/research.py`(신규): URL 정규화, 링크 수집, 제한적 검색, 캐시 조회, 예산 계산, 부분 결과 생성을 담당한다. 수집 원문은 예산에 맞춘 excerpt만 요약 단계에 전달한다.
- `backend/research_store.py`(신규): Info_Source, Research_Summary, Research_Run, cache를 S3 JSON으로 저장한다. `s3store.update_json()`의 optimistic concurrency를 사용한다.
- `backend/activity_store.py`(신규): Activity_Event를 월별 JSON append/update로 기록하고 기간별 조회를 제공한다. 조회는 현재 사용자 prefix 또는 관리자 승인 범위에서만 수행한다.
- `backend/drafting_engine.py`: 기존 Style_Profile·Vector_Index retrieval을 유지하고, 연구 자료를 별도 `researchContext` 영역으로 받는다. Style_Profile이 없으면 `generalStyle=true`로 기존 일반 문체 fallback을 사용한다. 연구 자료를 Style_Profile 또는 Post_Chunk로 저장하지 않는다.
- `backend/usage_meter.py`: 기존 월별 `metadata/usage-YYYY-MM.json`의 모델별 토큰·비용과 activity를 유지한다. 새 Research_Run의 `requests`, `inputChars`, `outputTokens`, `searchCalls`, `cacheHits`를 같은 사용자 usage 문서의 `research` 요약으로 누적한다. 기존 get-usage 응답은 하위 호환 필드를 유지하고 새 필드는 optional로 추가한다.
- `backend/errors.py`: 기존 `{error:{code,message}}` shape를 유지하며 `validation_error`, `not_found`, `forbidden`, `budget_exhausted`, `partial_result` 등은 기존 오류 체계에 맞춰 매핑한다. 부분 결과는 성공 HTTP 응답 안의 `status: partial`로 반환하고, 요청 자체를 처리할 수 없을 때만 오류 응답을 반환한다.

### 2.2 프론트엔드 계층

기존 `frontend/src/App.tsx`의 탭과 `ProfileView`를 유지한다. 새 화면은 기존의 단순 local state + `postJson` 패턴을 따른다. 전역 상태 라이브러리나 폼 라이브러리는 추가하지 않는다.

- `views/VoiceBoxView.tsx`(신규): VOC 개인 보관함의 입력, 검색/필터, 수정, 보관을 조합한다.
- `views/ResearchView.tsx`(신규): 링크 3~5개 입력, 제한적 검색 옵션, 예산 표시, 실행 상태, 부분 결과를 조합한다.
- `components/VocEditor.tsx`, `VocList.tsx`, `SourceLabel.tsx`, `ResearchSummary.tsx`(신규): 재사용 가능한 입력·목록·출처 표시 컴포넌트다.
- `components/ActivitySection.tsx`(신규): `UsageSection`과 함께 `ProfileView`에 배치한다. 스캐터/히트맵과 목록 대체 보기를 제공한다.
- `components/ActivityDetail.tsx`(신규): hover, focus, Enter/Space, touch click에서 동일한 상세 정보를 제공한다.
- `components/LearnedPostDialog.tsx`와 기존 `get-learning-post`, `rename-learning-post` 계약은 유지한다. 연구 결과의 원문/요약 보기와 스타일 학습 포스트 보기·제목 변경은 서로 다른 action과 데이터 모델을 사용한다.
- `DraftingView.tsx`는 연구 자료 선택 또는 Research_Run 결과 연결을 추가하되 기존 이미지 업로드, `imageKeys`, `GrantSelect`, `generalStyle` 표시를 변경하지 않는다.
- `ResultView.tsx`는 저장된 draft의 provenance metadata를 읽어 `SourceLabel`로 표시한다. Markdown 본문과 사실/생성 블록의 메타데이터는 분리 렌더링한다.

## Components and Interfaces

기존 고정 prefix와 사용자 prefix를 유지한다. 다음 신규 키는 모두 `s3store.user_key()`로 생성한다.

```text
users/{userId}/metadata/voc-index.json
users/{userId}/metadata/voc/{vocId}.json
users/{userId}/metadata/research-runs/{runId}.json
users/{userId}/metadata/research-summaries/{runId}.json
users/{userId}/metadata/info-sources/{sourceId}.json
users/{userId}/metadata/research-cache/{cacheKey}.json
users/{userId}/metadata/activity-{YYYY-MM}.json
users/{userId}/metadata/usage-{YYYY-MM}.json       # 기존 파일 확장
users/{userId}/generated-posts/{draftId}.md        # 기존 파일 유지
users/{userId}/metadata/drafts.json                # 기존 파일 확장
```

`raw-posts/`, `embeddings/`, `vector-index/`, `style-profile/`, `images/`, `generated-posts/`, `prompt-templates/`, `metadata/` 기존 원칙은 변경하지 않는다. 연구 원문은 `raw-posts/`에 저장하지 않고 `info-sources/` metadata의 제한된 excerpt 또는 별도 연구 metadata로만 보관한다. Info_Source는 Style_Learner가 읽는 raw post가 아니다.

### 3.1 VOC_Record

```json
{
  "v": 1,
  "vocId": "voc_01H...",
  "ownerId": "u_0123456789abcdef",
  "body": "사용자가 입력한 VOC 원문",
  "userMetadata": {
    "sourceType": "customer|observation|quote|request|other",
    "tags": ["온보딩", "모바일"],
    "note": "사용자 메모"
  },
  "status": "new",
  "createdAt": "2025-01-01T00:00:00.000Z",
  "updatedAt": "2025-01-01T00:00:00.000Z",
  "statusChangedAt": null,
  "statusChangedBy": null
}
```

원문(`body`)과 사용자 입력 메타데이터를 별도 필드로 보존한다. `ownerId`는 요청 본문이 아니라 Session_Token의 검증된 userId로 채운다. 목록 index에는 `vocId`, 제목/미리보기, status, createdAt, updatedAt만 저장해 목록 응답을 가볍게 한다.

### 3.2 Info_Source

```json
{
  "v": 1,
  "sourceId": "src_01H...",
  "runId": "run_01H...",
  "sourceType": "primary-link|web-search|voc",
  "title": "출처 제목",
  "domain": "example.com",
  "url": "https://example.com/article",
  "normalizedUrl": "https://example.com/article",
  "query": null,
  "collectedAt": "2025-01-01T00:00:00.000Z",
  "status": "succeeded|failed|skipped",
  "errorCode": null,
  "errorMessage": null,
  "excerpt": "예산 내에서 요약에 사용한 제한된 발췌",
  "sourceLabel": "fact|user-input"
}
```

검색 결과는 `sourceType=web-search`, `query`, `collectedAt`, `url`을 반드시 가진다. 수집 실패도 별도 source 항목으로 보존해 사용자가 어떤 링크가 제외됐는지 알 수 있게 한다.

### 3.3 Research_Run과 Research_Summary

```json
{
  "v": 1,
  "runId": "run_01H...",
  "ownerId": "u_0123456789abcdef",
  "status": "completed|partial|failed|budget-exhausted",
  "primarySourceIds": ["src_01H..."],
  "searchSourceIds": [],
  "summaryId": "summary_01H...",
  "requestedAt": "2025-01-01T00:00:00.000Z",
  "completedAt": "2025-01-01T00:00:10.000Z",
  "budget": {
    "maxRequests": 8,
    "maxInputChars": 24000,
    "maxOutputTokens": 1200,
    "maxSearchCalls": 2
  },
  "usage": {
    "requests": 3,
    "inputChars": 12000,
    "outputTokens": 800,
    "searchCalls": 0,
    "cacheHits": 1
  },
  "controls": ["search_not_requested"],
  "cacheKey": "sha256:..."
}
```

```json
{
  "v": 1,
  "summaryId": "summary_01H...",
  "runId": "run_01H...",
  "facts": [
    {
      "text": "출처에 근거한 요약 문장",
      "sourceIds": ["src_01H..."],
      "sourceLabel": "fact"
    }
  ],
  "generatedInterpretation": [
    { "text": "자료를 바탕으로 한 해석", "sourceLabel": "generated" }
  ],
  "unverified": [
    { "text": "출처를 특정할 수 없는 내용", "sourceLabel": "unverified" }
  ],
  "sourceIds": ["src_01H..."],
  "partial": true,
  "omitted": "보완 검색 예산 소진"
}
```

### 3.4 Activity_Event

```json
{
  "eventId": "evt_01H...",
  "actorUserId": "u_0123456789abcdef",
  "type": "voc-created|voc-updated|voc-status-changed|research-started|research-completed|draft-created",
  "occurredAt": "2025-01-01T00:00:00.000Z",
  "count": 1,
  "relatedVocId": "voc_01H...",
  "relatedRunId": null,
  "metadata": {
    "previousStatus": "reviewing",
    "newStatus": "confirmed"
  }
}
```

월별 문서는 `{v, month, events: []}` 구조로 저장한다. activity API는 일별 집계와 원본 event 요약을 함께 반환해 차트와 상세 패널을 같은 데이터로 구동한다. 관리자 조회는 `accessScope: {kind:"admin", requestedBy, filter}`를 audit metadata에 남긴다.

### 3.5 Draft provenance 확장

기존 `metadata/drafts.json` index와 `generated-posts/{draftId}.md`는 유지한다. 각 draft entry 또는 별도 `metadata/draft-provenance/{draftId}.json`에 다음을 추가한다.

```json
{
  "draftId": "draft_01H...",
  "researchRunId": "run_01H...",
  "infoSourceIds": ["src_01H..."],
  "segments": [
    { "kind": "fact", "sourceIds": ["src_01H..."] },
    { "kind": "generated", "sourceIds": [] }
  ],
  "generalStyle": false
}
```

출처를 특정할 수 없는 문장은 `unverified`로 저장하며 `fact`로 승격하지 않는다.

## Data Models

## 3. S3 저장 구조와 데이터 모델

모든 요청은 기존 `postJson()`을 통해 `action`이 포함된 JSON POST로 전달한다. 인증 action을 제외한 모든 action은 `X-Mablop-Token`을 사용한다. 성공 응답은 기존 `errors.response(200, result)` shape를 따른다.

### 4.1 VOC 개인·관리자 action

```text
create-voc
  { action, body, sourceType, tags, note }
  -> { voc }

list-voc
  { action, status?, from?, to?, query?, limit?, cursor? }
  -> { records, nextCursor? }

get-voc
  { action, vocId }
  -> { voc }

update-voc
  { action, vocId, body?, sourceType?, tags?, note?, status? }
  -> { voc }

archive-voc
  { action, vocId }
  -> { voc }

admin-list-voc
  { action, userId?, status?, from?, to?, limit?, cursor? }
  -> { records, nextCursor?, accessScope }

admin-update-voc-status
  { action, userId?, vocId, status }
  -> { voc, activityEvent }
```

일반 사용자의 `userId`는 무시한다. 관리자 action만 검증된 admin role과 운영 조회 범위를 사용한다. 사용 가능한 status는 정확히 `new`, `reviewing`, `confirmed`, `archived`다.

### 4.2 Research action

```text
run-research
  {
    action,
    links: [{ url }],                 // 3~5개
    search: { enabled, queries? },    // 명시적 요청일 때만
    budget: {
      maxRequests?, maxInputChars?, maxOutputTokens?, maxSearchCalls?
    },
    vocIds?: []
  }
  -> {
    runId, status, summary, sources,
    usage, budget, cacheHit, controls
  }

get-research-run
  { action, runId }
  -> { run, summary, sources }

list-research-runs
  { action, from?, to?, limit?, cursor? }
  -> { runs, nextCursor? }
```

기본 budget은 서버 설정값으로 정하고 요청 값은 서버 상한보다 클 수 없다. 링크가 3개 미만 또는 5개 초과이면 수집 함수와 Bedrock을 호출하지 않고 `bad_request`를 반환한다. 보완 검색을 요청하지 않으면 검색 provider를 호출하지 않는다. 링크 하나가 실패해도 유효한 결과를 반환하며 `status=partial`과 `controls`에 원인을 남긴다.

### 4.3 Activity action

```text
get-activity
  { action, from?, to?, granularity?: "day"|"hour" }
  -> {
    events: ActivityEvent[],
    daily: [{ date, total, byType, eventIds }],
    range: { from, to }
  }
```

서버는 기간과 최대 event 수를 제한한다. 프론트엔드는 `daily`로 히트맵을, `events`로 스캐터와 목록을 만든다. 응답에는 현재 acting user의 event만 포함한다.

### 4.4 Draft action 확장

기존 `generate-draft` 요청에 다음 optional 필드를 추가한다.

```text
{ action: "generate-draft", title, outline, notes, imageKeys,
  researchRunId?, infoSourceIds?, vocIds? }
```

응답은 기존 필드를 유지하고 다음을 optional로 추가한다.

```text
{ draftId, markdown, draftKey?, topK?, generalStyle,
  provenance: { infoSourceIds, segments },
  researchStatus?: "none"|"complete"|"partial"
}
```

`get-result`의 `kind=draft`는 기존 draft 본문과 provenance를 함께 반환한다. `get-learning-post`와 `rename-learning-post`는 기존 학습 포스트 조회·제목 변경 계약으로 유지하며 연구 자료와 공유하지 않는다.

## Error Handling

### 5.1 VOC

1. handler가 body length, rate, Session_Token, role을 검사한다.
2. `voc_store`가 token의 userId로 key를 생성하고 body의 identity/path는 버린다.
3. 입력 길이, tag 개수, enum을 검증한다.
4. 레코드와 index를 optimistic concurrency로 저장한다.
5. `voc-created` 또는 `voc-updated` event를 기록한다.
6. S3 오류는 기존 generic internal error로 반환하고, 저장 전 validation 오류는 명시적 bad request로 반환한다.

### 5.2 Research_Run

1. 링크를 trim·URL parse·fragment 제거·기본 포트 제거·host lowercase로 정규화한다.
2. 링크 cardinality를 검사하고 cache key를 `sha256(userId + normalizedLinks + summarySettings + budgetProfile)`로 계산한다. 원문 URL 순서는 정렬해 같은 집합이 같은 key를 갖게 한다.
3. 유효 cache가 있으면 source/summary를 반환하고 `cacheHit=true`로 기록한다.
4. cache miss면 primary 링크를 순서대로 수집한다. 각 단계 전에 요청·문자 수 budget을 확인한다.
5. 사용자가 명시한 경우에만 제한된 검색을 실행하며 `maxSearchCalls`를 넘지 않는다.
6. 수집된 excerpt를 문자/토큰 cap으로 자르고 summary prompt에 넣는다. 원문 전체와 사용자 자료는 system instruction과 구분된 untrusted data block으로 전달한다.
7. Bedrock Grant가 확인된 경우에만 요약을 호출한다. Grant가 없으면 403이며 수집 결과를 임의로 생성하지 않는다.
8. 성공·실패 source와 summary를 저장하고 cache 만료 시각을 기록한다. 일부 성공은 `partial`이다.
9. budget 또는 Lambda 시간 guard에 도달하면 이후 호출을 중지하고 확보한 source/summary와 `controls`를 반환한다.
10. 수집 네트워크 오류, 검색 오류, 요약 오류는 개별 단계 오류로 기록한다. primary 자료가 하나라도 있으면 partial 결과를 우선하고, 아무 결과도 없으면 `research_failed` 오류를 반환한다.

### 5.3 Drafting 통합

`drafting_engine.generate_draft()`는 기존 입력을 먼저 처리한 뒤 `researchRunId`를 현재 사용자 prefix에서 조회한다. Info_Source는 `RESEARCH_CONTEXT`에, Style_Profile과 Top_K Post_Chunk는 `STYLE_CONTEXT`에 넣는다. 둘은 prompt 안에서 별도 경계를 갖고 source label과 storage area를 유지한다. 유효 Style_Profile이 없거나 retrieval 결과가 비면 `STYLE_CONTEXT`를 비우고 일반 문체 fallback을 사용한다. 연구 자료는 fallback에서도 정보 영역으로만 사용한다.

## 6. React 컴포넌트·브랜드·접근성 설계

### 6.1 화면 배치

- `App.tsx`에 `voice`와 `research` 탭을 추가하거나, 기존 `style`·`result` 흐름을 방해하지 않도록 `마이페이지` 안에 Voice Box와 활동을 배치한다. 기본 탭은 기존 `style`을 유지한다.
- `VoiceBoxView`는 상단에 새 VOC 입력, 아래에 상태 필터·기간 필터·목록을 둔다. 모바일에서는 목록 행을 카드로 전환한다.
- `ResearchView`는 링크 입력 3~5개를 명시하고, 검색 보완은 기본 해제된 disclosure/checkbox로 둔다. 예산 사용량과 남은 한도를 실행 버튼 근처에 표시한다.
- `ResearchSummary`는 `사실`, `생성`, `사용자 입력`, `출처 미확인`을 텍스트 배지와 설명으로 표시한다. 사실에는 제목 또는 도메인과 안전한 external link를 함께 표시한다.
- `ActivitySection`은 기존 `UsageSection` 아래에 배치한다. 데스크톱은 SVG 또는 CSS grid 기반 scatter/heatmap, 모바일은 동일 event의 세로 목록을 제공한다.
- 기존 `LearnedPostDialog`는 학습 포스트 본문 보기와 제목 변경을 계속 담당한다. 연구 요약은 별도 dialog/panel을 사용해 기능과 출처를 혼동하지 않는다.

### 6.2 활동 상세 상호작용

각 scatter point와 heatmap cell은 `button` 또는 `tabIndex=0`인 접근 가능한 요소로 만들고 `aria-label`에 날짜·활동 유형·횟수를 포함한다. `pointerenter`/`pointerleave`에서는 detail을 보이되, `focus`, `Enter`, `Space`, `click`에서는 고정 panel을 토글한다. 터치에서는 hover에 의존하지 않고 탭으로 열고 닫는다. 모든 상세에는 `relatedVocId` 또는 `relatedRunId` 링크/식별자를 포함한다. 색상 강도는 보조 수단이며 숫자와 텍스트 범례를 항상 노출한다.

### 6.3 펭귄 색연필 브랜드 방향

참조 이미지는 원본을 복제하는 대신 다음 토큰으로 번역한다.

```css
:root {
  --brand-ink: #24312d;
  --brand-green: #5f9270;
  --brand-green-dark: #3f6f51;
  --brand-paper: #fbf7ef;
  --brand-pencil-yellow: #e8c96a;
  --brand-pencil-coral: #d98268;
  --brand-pencil-blue: #7aa5b4;
  --brand-line: #ded6c8;
}
```

Pretendard, 따뜻한 중립 배경, 단일 녹색 primary action, 무그라데이션, lucide-react 아이콘 원칙을 유지한다. 펭귄·연필 모티프는 빈 상태 일러스트, 작은 section accent, favicon/브랜드 영역처럼 정보 계층을 가리지 않는 곳에만 사용한다. 본문, 출처 링크, 상태 배지, 오류 메시지는 장식보다 높은 대비와 읽기 순서를 가진다. 원본 이미지가 배포되지 않아도 CSS 색상·손그림 질감의 단순 패턴·캐릭터 분위기만으로 화면이 완성되게 한다.

### 6.4 모바일 접근성

320px 폭부터 가로 overflow가 없도록 `min-width`를 강제하지 않는다. 링크 목록·출처 표는 카드/세로 흐름으로 바꾸고, activity 차트는 `overflow-x: auto`와 목록 대체 보기를 함께 제공한다. 입력은 label과 `aria-describedby`를 갖고 오류는 `role=alert`, 진행 상태는 `role=status`로 표시한다. 상태·출처·범례는 색상만으로 구분하지 않고 텍스트를 포함한다. 포커스 순서는 입력 → 예산/옵션 → 실행 → 결과/출처 순으로 유지한다. 200% 확대에서 버튼과 상세 panel이 잘리지 않도록 한다.

## 7. 보안·비용·운영 제약

- 모든 신규 action은 기존 Access_Controller를 먼저 통과한다. 인증 실패는 기존 401, role 부족은 403으로 통일한다.
- `user_scope.bind()` 밖에서 `s3store.user_key()`를 호출하지 않는다. 관리자 action도 명시된 admin filter 범위만 허용하며 결과에 `accessScope`를 기록한다.
- URL은 `http/https`만 허용하고 localhost, private IP, link-local IP, 비정상 포트 등 SSRF 위험 주소를 차단한다. 수집 응답의 content type과 최대 바이트를 제한한다.
- 수집 HTML/사용자 VOC/검색 결과는 untrusted content로 취급하고 system prompt와 구분한다. 모델 출력은 사실로 자동 승격하지 않는다.
- S3는 private + SSE를 유지하고 IAM에는 신규 user metadata prefix와 필요한 S3 동작만 추가한다. 브라우저 bundle에는 boto3, Bedrock SDK, AWS 자격 증명을 넣지 않는다.
- 기본 research budget은 서버 설정으로 고정하며 요청이 상한을 높이지 못하게 한다. cache TTL은 source fingerprint와 함께 검사한다. 비용 기록은 `usage_meter.record()`가 실패해도 본 작업을 중단하지 않는 기존 원칙을 따른다.
- Lambda 제한을 넘길 것으로 예상되면 단계 전환 전 guard를 실행하고 작업을 중지한다. Step Functions 등 제외 서비스는 추가하지 않는다.

## Correctness Properties

### Property 1: 사용자별 저장 격리

**For any** 인증된 사용자와 VOC·Info_Source·Research_Run·Cache·Activity_Event 입력에 대해, 생성·조회 결과와 모든 S3 key는 검증된 acting user의 `users/{userId}/` prefix만 사용하고 다른 사용자의 레코드를 반환하지 않아야 한다.

**Validates: Requirements 1.1, 1.2, 10.1, 13.3**

### Property 2: VOC 원문·메타데이터 보존

**For any** 유효한 VOC 원문, 출처 유형, 태그, 메모를 저장하고 다시 조회하면 원문과 사용자 입력 메타데이터가 별도 필드로 동일하게 보존되어야 한다.

**Validates: Requirements 1.3**

### Property 3: 상태 값과 변경 이벤트 불변식

**For any** 관리자에 의한 유효 상태 전이에서 저장되는 status는 `new`, `reviewing`, `confirmed`, `archived` 중 하나이고, 이벤트에는 actor, 이전 상태, 새 상태, 변경 시각이 포함되어야 한다.

**Validates: Requirements 1.5, 2.1, 2.4**

### Property 4: 링크 집합 cardinality와 source 기록

**For any** 링크 집합에 대해 3개 이상 5개 이하일 때만 Research_Run이 실행되며, 처리된 각 링크에는 접근 결과, 정규화 URL, 수집 시각, source 식별자가 기록되어야 한다.

**Validates: Requirements 4.1, 4.2, 4.3**

### Property 5: 부분 수집 보존

**For any** primary 링크 집합의 성공·실패 조합에 대해 성공한 source 결과는 보존되고 실패한 source에는 상태와 원인이 기록되며, 하나 이상의 성공 결과가 있으면 run은 부분 결과를 반환해야 한다.

**Validates: Requirements 4.5, 5.5, 7.5**

### Property 6: 검색 명시성과 횟수 상한

**For any** Research_Run 요청에 대해 검색이 명시되지 않으면 Web_Search 호출 수는 0이고, 검색이 명시되면 호출 수는 해당 Cost_Budget의 `maxSearchCalls` 이하이며 검색 source에는 query, 실행 시각, 결과 URL이 포함되어야 한다.

**Validates: Requirements 5.1, 5.2, 5.3, 5.4**

### Property 7: 예산 내 실행과 조기 중단

**For any** Cost_Budget와 단계별 사용량 조합에 대해 Backend는 requests, input chars, output tokens, search calls 각각의 상한을 넘는 단계를 실행하지 않고, 상한에 도달하면 이미 생성한 결과와 중단 사유를 반환해야 한다.

**Validates: Requirements 7.1, 7.5, 13.5**

### Property 8: 캐시 정규화와 유효성

**For any** 같은 사용자, 같은 정규화 링크 집합, 같은 요약 설정의 Research_Run 재실행은 유효 cache를 재사용하고, cache가 만료되었거나 원본 fingerprint가 바뀌면 새 run과 새 만료 시각을 만들어야 한다.

**Validates: Requirements 7.2, 7.3**

### Property 9: 연구 자료와 스타일 자료 분리

**For any** VOC·Info_Source·Style_Profile이 함께 포함된 draft 또는 학습 요청에 대해 VOC와 Info_Source는 정보 영역에만 들어가고 Style_Profile·Post_Chunk는 스타일 영역에만 들어가며, 각 자료의 source label과 원래 저장 영역이 보존되어야 한다.

**Validates: Requirements 6.1, 6.2, 6.3, 6.4, 8.3**

### Property 10: 일반 문체 fallback

**For any** 유효하지 않거나 비어 있는 Style_Profile 또는 style retrieval 결과에 대해 Drafting_Engine은 일반 문체로 생성하고 `generalStyle=true`를 반환하며, Info_Source와 VOC가 있으면 정보 입력으로만 사용해야 한다.

**Validates: Requirements 8.1, 8.2, 8.3**

### Property 11: 제한된 retrieval과 prompt 입력

**For any** 스타일 프로필과 Post_Chunk 집합에 대해 retrieval 결과 수는 Top_K 이하이고, prompt의 style context와 research excerpt 입력량은 context token cap과 research budget 이하이어야 한다.

**Validates: Requirements 7.4, 8.4**

### Property 12: 출처 표시 안전성

**For any** fact, generated, user-input, unverified 콘텐츠를 렌더링할 때 Frontend는 해당 Source_Label을 텍스트로 표시하고, fact에만 연결된 제목/도메인/링크를 표시하며, 출처 불명 콘텐츠를 fact로 표시하지 않아야 한다.

**Validates: Requirements 9.1, 9.2, 9.3, 9.5**

### Property 13: 활동 상세의 입력 방식 불변성

**For any** Activity_Event 점 또는 셀에 대해 pointer hover, keyboard focus/activation, touch click 중 어느 방식으로 열어도 날짜 또는 시간대, 활동 유형, 활동 수, 관련 VOC 또는 Research_Run 식별자를 같은 상세 모델로 제공해야 한다.

**Validates: Requirements 3.2, 3.5, 12.4**

### Property 14: 활동 시각화의 사용자 격리와 접근 가능한 설명

**For any** 혼합 사용자 activity dataset에 대해 acting User의 이벤트만 차트·히트맵·목록에 나타나고, 각 시각화에는 색상과 독립적인 텍스트 범례·accessible name·상세 정보가 있어야 한다.

**Validates: Requirements 3.1, 3.3**

### Property 15: 반응형 핵심 기능 보존

**For any** 지원 화면 폭에서 VOC 입력, 연구 결과, 출처 라벨, activity 상세에 접근할 수 있고, 좁은 폭에서는 수평 잘림 없이 세로·스크롤·목록 대체 레이아웃 중 하나로 같은 정보를 확인할 수 있어야 한다.

**Validates: Requirements 3.4, 12.1, 12.2, 12.3, 12.5**

## Testing Strategy

### 9.1 백엔드 단위·property 테스트

- `voc_store`: Hypothesis로 Unicode 원문, tag, 상태, owner를 생성해 round-trip·enum·activity event property를 검증한다.
- `research.py`: URL normalization, cardinality, cache key, budget evaluator, excerpt truncation, partial aggregation을 순수 함수로 분리해 각 property를 최소 100회 실행한다.
- `drafting_engine`: Bedrock/S3를 mock하고 style/research context 분리, Top_K, token cap, fallback metadata를 검증한다.
- `activity_store`: 날짜 집계, 사용자 필터, event detail DTO를 검증한다.

property 테스트 태그는 다음 형식을 사용한다.

```text
Feature: voice-box-content-research-branding, Property N: <설계 속성 본문>
```

### 9.2 백엔드 통합·회귀 테스트

- `handler`에 실제 action JSON과 mock S3/auth context를 넣어 401, 403, validation, partial, budget exhausted 응답을 검증한다.
- 기존 `backend/tests`의 auth, grant, style learner, draft history, usage meter 테스트를 변경 없이 통과시킨다.
- S3 key, private/SSE/IAM, 제외 서비스는 `infra/tests` 정적 검사를 확장한다.

### 9.3 프론트엔드 테스트

- `api.test.ts` 패턴으로 새 action response/error shape를 contract test한다.
- React Testing Library로 VOC 입력/수정/보관, 링크 3~5개 validation, 검색 checkbox, partial 결과, SourceLabel, draft provenance를 테스트한다.
- ActivitySection은 hover뿐 아니라 focus, Enter/Space, click/touch를 테스트하고 100개 이상의 event fixture로 detail mapping property를 실행한다.
- 320px·375px·데스크톱 viewport에서 layout snapshot/overflow 검사를 하고, axe 기반 focus/name/contrast 검사를 수행한다.
- `App`의 기존 탭, `StyleLearningView`, `DraftingView`, `ResultView`, `UsageSection` 회귀 테스트를 유지한다.

### 9.4 수동 검토

펭귄 색연필 참조의 분위기, 정보 계층, 출처 라벨의 시각적 명확성, 200% 확대와 실제 터치 환경의 사용성을 대표 화면에서 검토한다. 미감은 자동 property로 대체하지 않고 디자인 토큰, snapshot, 접근성 검사와 함께 검토한다.
