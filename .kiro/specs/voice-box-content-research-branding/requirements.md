# Requirements Document

> 이 문서는 한국어로 작성된 요구사항 문서입니다.

## Introduction

`voice-box-content-research-branding`은 MABLOP에 개인 VOC 보관함, 저비용 콘텐츠 정보 수집, 활동 분석, 사실 기반 출처 표시, 브랜드 방향, 모바일 접근성을 추가하는 기능이다. 이 기능은 사용자의 콘텐츠 연구와 브랜드 표현을 돕되, 기존 블로그 스타일 학습과 AI 초안 생성의 저장·인증·호출 경계를 변경하지 않는다.

### 범위와 기존 요구사항과의 관계

- 기존 `mablop-mvp`의 AWS 리전 `us-east-1`, S3 단일 저장소, Python Lambda Function URL, React/GitHub Pages, Bedrock 호출 경계, numpy 코사인 검색, 비용 목표, 이미지 10개 상한, 비공개 버킷과 암호화 요구사항을 유지한다.
- 기존 `user-auth-admin`의 Session_Token, 사용자별 `users/{userId}/` 저장 경로, 관리자 승인·비활성화 모델, Bedrock Grant 검사를 유지한다. 새 데이터는 항상 인증된 사용자와 연결된 사용자 prefix 안에 저장한다.
- 기존 스타일 학습은 블로그 글을 **스타일 학습 소스**로 사용한다. VOC와 링크·검색 자료는 스타일 학습 소스에 자동 편입하지 않으며, 콘텐츠 연구의 **정보 소스**로만 사용한다.
- 이번 문서는 VOC·연구·브랜딩·표현 UX의 요구사항만 정의한다. 기존 인증, 계정, 레거시 데이터 이전, 블로그 크롤링 상세, 초안 생성 프롬프트 구현, 배포 및 인프라 구현은 변경하지 않는다.
- 제공된 펭귄 색연필 이미지는 실제 파일 경로를 이 문서에서 새로 정하지 않고, 브랜드 방향을 결정하는 참조 자산으로 취급한다.

## Glossary

- **MABLOP_System**: 기존 MABLOP 프론트엔드, Python Lambda 백엔드, S3 저장소, Bedrock 연동을 포함한 전체 시스템.
- **Frontend**: GitHub Pages에서 제공되는 React(TypeScript) 사용자 인터페이스.
- **Backend**: 공개 Lambda Function URL 뒤에서 인증, 저장, 정보 수집, 초안 관련 요청을 처리하는 Python Lambda.
- **VOC**: 사용자가 직접 입력하거나 수집해 보관하는 고객의 목소리, 반응, 관찰, 요구, 인용 메모.
- **VOC_Vault**: 한 사용자의 VOC를 생성·조회·수정·보관하는 개인 보관함.
- **VOC_Record**: 원문, 작성 시각, 출처 유형, 태그, 상태, 사실성 표시를 포함하는 하나의 VOC 항목.
- **VOC_Status**: 관리자가 관리하는 VOC 상태인 `new`, `reviewing`, `confirmed`, `archived`.
- **Activity_Event**: VOC 작성·수정·상태 변경·연구 실행 같은 사용자 활동을 시각과 유형으로 기록한 이벤트.
- **Activity_Visualization**: Activity_Event를 스캐터 플롯과 히트맵으로 표현하는 화면.
- **Hover_Detail**: Activity_Visualization의 점 또는 셀에 포인터를 올렸을 때 표시하는 세부 정보.
- **Info_Source**: 콘텐츠 연구에 사용되는 링크, 링크에서 추출한 자료, 제한적 웹 검색 결과.
- **Style_Source**: 사용자의 문체를 학습하기 위해 기존 Style_Learner가 처리하는 블로그 본문 또는 붙여넣은 글.
- **Research_Run**: 여러 Info_Source를 수집·요약·캐시하는 하나의 사용자 요청 실행.
- **Primary_Link_Set**: 사용자가 직접 제공한 3~5개의 링크 집합. 저비용 정보 수집의 기본 입력이다.
- **Web_Search**: Primary_Link_Set을 보완하기 위해 제한적으로 실행하는 별도 검색 경로.
- **Research_Summary**: Info_Source에서 추출한 핵심 정보, 출처 목록, 사실·생성 구분을 포함한 요약 결과.
- **Fact_Content**: 원문 또는 출처에 근거해 확인 가능한 내용.
- **Generated_Content**: 모델이 요약·해석·제안·초안으로 생성한 내용.
- **Source_Label**: 콘텐츠가 `사실`, `생성`, `사용자 입력` 중 무엇인지 나타내는 표시.
- **Cost_Budget**: Research_Run에 적용하는 요청 수, 입력량, 출력량, 검색 실행 수의 설정 상한.
- **Cache**: 동일 사용자와 동일한 정규화 입력에 대해 재사용하는 만료 가능한 수집·요약 결과.
- **Brand_Reference_Image**: 사용자가 제공한 펭귄 색연필 이미지로, 색상·질감·캐릭터 분위기를 판단하는 브랜드 참조 자산.
- **Admin_User**: 기존 `user-auth-admin`에서 승인·비활성화 권한을 가진 관리자 계정.
- **User_Prefix**: 기존 사용자별 S3 경로인 `users/{userId}/`.
- **Bedrock_Grant**: 기존 사용자별 Bedrock 사용 승인 기록.

## Requirements

### Requirement 1: VOC 개인 보관함

**User Story:** 사용자는 자신의 VOC를 안전하게 모으고 다시 활용하고 싶다.

#### Acceptance Criteria

1. WHEN 인증된 사용자가 VOC를 저장하면, THE VOC_Vault SHALL 해당 VOC_Record를 acting User의 User_Prefix 아래에 저장하고 사용자 식별자와 연결한다.
2. WHEN 사용자가 VOC_Vault를 조회하면, THE VOC_Vault SHALL 다른 사용자의 VOC_Record를 포함하지 않고 acting User의 VOC_Record만 반환한다.
3. WHEN 사용자가 VOC 원문, 태그, 출처 유형 또는 메모를 입력하면, THE VOC_Vault SHALL 원문과 사용자 입력 메타데이터를 별도 필드로 보존한다.
4. IF 인증 정보가 유효하지 않거나 User_Prefix가 acting User의 prefix와 일치하지 않으면, THEN THE Backend SHALL VOC 데이터에 접근하지 않고 인증 또는 권한 오류를 반환한다.
5. WHEN 사용자가 VOC_Record를 수정하거나 보관 처리하면, THE VOC_Vault SHALL 수정 시각과 변경된 상태를 기록한다.

### Requirement 2: VOC 상태 관리

**User Story:** 관리자는 검토가 필요한 VOC를 분류하고 운영 상태를 관리하고 싶다.

#### Acceptance Criteria

1. WHEN Admin_User가 VOC_Record의 상태를 변경하면, THE Backend SHALL `new`, `reviewing`, `confirmed`, `archived` 중 하나의 VOC_Status만 저장한다.
2. WHEN Admin_User가 VOC_Record를 조회하면, THE Backend SHALL 상태별 필터와 작성 시각 범위를 적용해 결과를 반환한다.
3. IF 일반 사용자가 다른 사용자의 VOC_Status를 변경하려고 하면, THEN THE Backend SHALL 변경을 저장하지 않고 관리자 권한 오류를 반환한다.
4. WHEN VOC_Status가 변경되면, THE Backend SHALL 변경 주체, 이전 상태, 새 상태, 변경 시각을 Activity_Event로 기록한다.

### Requirement 3: 활동 시각화와 hover 세부 정보

**User Story:** 사용자는 VOC와 연구 활동의 흐름을 한눈에 파악하고 개별 활동을 자세히 확인하고 싶다.

#### Acceptance Criteria

1. WHEN 사용자가 활동 화면을 열면, THE Frontend SHALL acting User의 Activity_Event만 스캐터 플롯과 히트맵으로 표시한다.
2. WHEN 사용자가 스캐터의 점 또는 히트맵의 셀에 포인터를 올리거나 키보드 포커스를 이동하면, THE Frontend SHALL 날짜 또는 시간대, 활동 유형, 활동 수, 관련 VOC 또는 Research_Run 식별 정보를 Hover_Detail로 표시한다.
3. WHEN Activity_Visualization이 표시되면, THE Frontend SHALL 색상만으로 활동 유형이나 강도를 전달하지 않고 텍스트 범례와 접근 가능한 이름을 함께 제공한다.
4. IF 활동 데이터가 없거나 선택한 기간에 활동이 없으면, THEN THE Frontend SHALL 빈 상태 설명과 기간 변경 방법을 표시한다.
5. WHEN 모바일 화면 폭에서 활동 화면을 표시하면, THE Frontend SHALL 가로 스크롤, 축소 대체 보기 또는 목록 보기를 제공해 Hover_Detail의 내용을 확인할 수 있게 한다.

### Requirement 4: 링크 기반 저비용 정보 수집

**User Story:** 사용자는 적은 비용으로 신뢰할 자료를 모아 콘텐츠를 준비하고 싶다.

#### Acceptance Criteria

1. WHEN 사용자가 Research_Run을 시작하고 Primary_Link_Set을 제공하면, THE Backend SHALL 3개 이상 5개 이하의 링크를 기본 정보 입력으로 처리한다.
2. WHEN Primary_Link_Set의 링크를 처리하면, THE Backend SHALL 각 링크의 접근 결과, 정규화 URL, 수집 시각, 요약에 사용된 출처를 기록한다.
3. IF 사용자가 제공한 링크가 3개 미만이거나 5개를 초과하면, THEN THE Backend SHALL Research_Run을 실행하지 않고 허용 범위를 안내하는 입력 오류를 반환한다.
4. WHEN 링크 수집과 요약이 완료되면, THE Backend SHALL Research_Summary와 Info_Source 목록을 사용자별 저장 경로에 보관한다.
5. IF 링크 하나의 수집이 실패하면, THEN THE Backend SHALL 다른 유효 링크의 결과를 보존하고 실패한 링크의 상태와 원인을 요약 결과에 표시한다.

### Requirement 5: 제한적 웹 검색 보완 경로

**User Story:** 사용자는 직접 제공한 링크만으로 부족한 부분을 제한적으로 보완하고 싶다.

#### Acceptance Criteria

1. WHERE 사용자가 보완 검색을 명시적으로 요청하면, THE Backend SHALL Web_Search를 Primary_Link_Set 수집과 분리된 Research_Run 단계로 실행한다.
2. WHERE 사용자가 보완 검색을 명시적으로 요청하면, THE Backend SHALL Cost_Budget 안에서 설정된 최대 검색 실행 수를 초과하지 않는다.
3. IF 사용자가 보완 검색을 요청하지 않으면, THEN THE Backend SHALL Web_Search를 실행하지 않고 Primary_Link_Set만으로 Research_Summary를 생성한다.
4. WHEN Web_Search 결과가 요약에 포함되면, THE Backend SHALL 검색 결과를 별도 Info_Source 유형으로 표시하고 검색어, 실행 시각, 결과 링크를 함께 보관한다.
5. IF Web_Search가 실패하거나 Cost_Budget을 소진하면, THEN THE Backend SHALL Primary_Link_Set 기반 결과를 반환하고 보완 검색이 제외되었음을 표시한다.

### Requirement 6: 스타일 학습 소스와 정보 소스 분리

**User Story:** 사용자는 연구 자료가 자신의 문체를 오염시키지 않으면서 콘텐츠 작성에 활용되기를 원한다.

#### Acceptance Criteria

1. THE Style_Learner SHALL Style_Source만 문체 학습 입력으로 사용하고 VOC 또는 Info_Source를 Style_Source로 변환하지 않는다.
2. THE Backend SHALL Research_Summary, Info_Source, VOC_Record를 기존 Style_Profile, Vector_Index, Post_Chunk 저장 구조에 기록하지 않는다.
3. WHEN Drafting_Engine이 콘텐츠 연구 자료를 사용하는 요청을 처리하면, THE Drafting_Engine SHALL Info_Source와 Style_Profile을 서로 다른 입력 영역으로 전달한다.
4. IF 연구 자료와 스타일 학습 자료가 같은 요청에 포함되면, THEN THE Backend SHALL 각 자료의 Source_Label과 원래 저장 영역을 유지해 출처 유형을 구분한다.
5. WHEN 사용자가 기존 방식으로 스타일 재학습을 실행하면, THE Style_Learner SHALL 기존 `MAX_POST_COUNT`, 체크포인트, 사용자별 저장 경로, Bedrock Grant 검사를 그대로 적용한다.

### Requirement 7: 비용 상한, 캐시, 요약

**User Story:** 시스템 소유자는 정보 수집과 요약 비용을 예측 가능한 범위로 유지하고 싶다.

#### Acceptance Criteria

1. THE Backend SHALL 각 Research_Run에 Cost_Budget을 적용해 링크 수, 입력 문자 또는 토큰 수, 요약 출력량, Web_Search 실행 수를 제한한다.
2. WHEN 동일 사용자가 동일한 정규화 링크 집합과 동일한 요약 설정으로 Research_Run을 재실행하면, THE Backend SHALL 유효한 Cache를 우선 사용하고 중복 수집·요약을 줄인다.
3. WHEN Cache가 만료되었거나 원본 수집 상태가 변경되면, THE Backend SHALL 새 Research_Run을 실행하고 새 만료 시각을 기록한다.
4. WHEN Backend가 수집 자료를 요약하면, THE Backend SHALL 원문 전체를 반복 전송하지 않고 Cost_Budget 안에서 필요한 정보만 요약 입력으로 사용한다.
5. IF Cost_Budget에 도달하면, THEN THE Backend SHALL 추가 수집·검색·생성을 중지하고 사용한 비용 제어 항목과 부분 결과를 반환한다.

### Requirement 8: 일반 문체 fallback

**User Story:** 사용자는 충분한 스타일 학습 자료가 없어도 읽을 수 있는 초안을 받고 싶다.

#### Acceptance Criteria

1. IF acting User에게 유효한 Style_Profile이 없거나 스타일 검색 결과가 없으면, THEN THE Drafting_Engine SHALL 일반 문체 fallback으로 초안을 생성한다.
2. WHEN 일반 문체 fallback이 적용되면, THE Drafting_Engine SHALL 응답 메타데이터에 fallback 적용 여부를 표시한다.
3. IF Style_Profile이 유효하지 않지만 Info_Source 또는 VOC 자료가 있으면, THEN THE Drafting_Engine SHALL 해당 자료를 정보 입력으로 사용하고 문체 입력으로 사용하지 않는다.
4. WHEN Style_Profile이 유효하면, THE Drafting_Engine SHALL 기존 Top_K 검색과 context token cap을 적용해 사용자 문체를 우선한다.

### Requirement 9: 콘텐츠 출처 표시와 사실·생성 구분

**User Story:** 사용자는 무엇이 출처에 근거하고 무엇이 모델의 생성인지 구분해 검토하고 싶다.

#### Acceptance Criteria

1. WHEN Research_Summary 또는 Drafting_Engine 결과가 화면에 표시되면, THE Frontend SHALL Fact_Content, Generated_Content, 사용자 입력을 Source_Label로 구분한다.
2. WHEN Fact_Content가 표시되면, THE Frontend SHALL 연결된 Info_Source의 제목 또는 도메인과 링크를 함께 표시한다.
3. WHEN Generated_Content가 표시되면, THE Frontend SHALL 모델 생성 또는 해석 결과임을 표시하고 Fact_Content처럼 표현하지 않는다.
4. WHEN Draft가 저장되면, THE Backend SHALL 사용된 Info_Source 식별자와 생성된 부분의 Source_Label 메타데이터를 Draft와 함께 저장한다.
5. IF 콘텐츠의 출처를 특정할 수 없으면, THEN THE Backend SHALL 해당 콘텐츠를 Fact_Content로 표시하지 않고 출처 미확인 상태로 표시한다.

### Requirement 10: 사용자별 격리와 접근 제어

**User Story:** 사용자는 자신의 VOC, 연구 결과, 활동 기록이 다른 사용자에게 노출되지 않기를 원한다.

#### Acceptance Criteria

1. THE Backend SHALL VOC_Record, Info_Source, Research_Summary, Cache, Activity_Event를 acting User의 User_Prefix에 저장하고 조회한다.
2. IF 요청 본문이 다른 userId, loginId 또는 S3 경로를 제공하면, THEN THE Backend SHALL 해당 값을 권한 판단에 사용하지 않고 검증된 Session_Token의 userId만 사용한다.
3. IF 사용자가 Bedrock을 호출하는 Research_Run 또는 초안 생성 요청을 실행하면, THEN THE Backend SHALL 기존 Bedrock_Grant를 확인한 뒤에만 Bedrock 호출을 허용한다.
4. THE MABLOP_System SHALL 기존 비공개 S3, 서버 측 암호화, 최소 권한 IAM, 브라우저의 직접 Bedrock 호출 금지 요구사항을 유지한다.
5. WHEN Admin_User가 운영 목적의 VOC 상태 조회를 수행하면, THE Backend SHALL 관리자 권한을 확인하고 조회 결과에 접근 범위를 기록한다.

### Requirement 11: 펭귄 색연필 이미지 브랜드 방향

**User Story:** 제품 운영자는 제공된 펭귄 색연필 이미지를 바탕으로 일관된 브랜드 인상을 만들고 싶다.

#### Acceptance Criteria

1. THE Frontend SHALL Brand_Reference_Image에서 식별되는 펭귄 캐릭터, 색연필 질감, 손그림 느낌을 브랜드 시각 방향의 참조로 사용한다.
2. THE Frontend SHALL 브랜드 방향을 따뜻하고 친근한 색상 체계, 명확한 대비, 절제된 장식으로 표현하고 기존 Calm_Design_Guide의 Pretendard, 따뜻한 중립색, 단일 녹색 포인트, 무그라데이션, lucide-react 아이콘 원칙을 유지한다.
3. WHEN 브랜드 참조 요소를 화면에 적용하면, THE Frontend SHALL 본문 가독성과 콘텐츠 출처 표시를 우선하고 장식 요소가 정보 계층을 가리지 않게 한다.
4. IF Brand_Reference_Image의 원본 파일을 제공할 수 없는 화면이면, THEN THE Frontend SHALL 이미지 자체를 복제하지 않고 정의된 색상·질감·캐릭터 분위기만 사용한다.

### Requirement 12: 모바일 접근성과 반응형 UX

**User Story:** 사용자는 모바일에서도 VOC 입력, 연구 결과 확인, 출처 검토, 활동 분석을 수행하고 싶다.

#### Acceptance Criteria

1. THE Frontend SHALL 모바일과 데스크톱 화면 폭에서 VOC_Vault, Research_Summary, Source_Label, Activity_Visualization의 핵심 기능을 제공한다.
2. WHEN 화면 폭이 좁아지면, THE Frontend SHALL 입력 폼, 출처 목록, 요약, 상태 조작을 가로 잘림 없이 재배치하고 필요한 경우 세로 흐름으로 전환한다.
3. THE Frontend SHALL 키보드 포커스, 포커스 순서, 텍스트 대체 설명, 명확한 오류 메시지, 충분한 색상 대비를 제공한다.
4. WHEN hover를 사용할 수 없는 터치 환경이면, THE Frontend SHALL 탭 또는 키보드 포커스로 Hover_Detail과 동일한 세부 정보를 열고 닫게 한다.
5. IF 사용자가 화면 확대 또는 보조 기술을 사용하면, THEN THE Frontend SHALL Source_Label, VOC_Status, 차트 범례, 오류 상태를 의미가 보존된 텍스트로 제공한다.

### Requirement 13: 기존 기능 및 범위 경계

**User Story:** 시스템 소유자는 새 기능이 기존 MVP의 범위와 비용 목표를 벗어나지 않기를 원한다.

#### Acceptance Criteria

1. THE MABLOP_System SHALL 이번 기능을 기존 React 프론트엔드, Python Lambda Backend, 단일 비공개 S3 저장소, 기존 인증 경로 안에서 제공한다.
2. THE MABLOP_System SHALL API Gateway, DynamoDB, Cognito, Step Functions, Bedrock Agent 또는 Knowledge Base, OpenSearch, RDS, EC2, ECS, Fargate, Kubernetes, 헤드리스 브라우저 크롤링을 새 기능의 필수 구성요소로 추가하지 않는다.
3. THE MABLOP_System SHALL 기존 `raw-posts/`, `embeddings/`, `vector-index/`, `style-profile/`, `images/`, `generated-posts/`, `prompt-templates/`, `metadata/` 저장 원칙을 유지하고 새 사용자별 자료는 User_Prefix 아래에 둔다.
4. THE MABLOP_System SHALL 이번 기능으로 기존 블로그 스타일 학습, 이미지 업로드·캡션, Markdown 초안 저장, 사용자 인증·관리자 승인, GitHub Actions OIDC 배포의 수용 기준을 변경하지 않는다.
5. IF 이번 기능의 연구·요약·시각화가 기존 비용 상한 또는 Lambda 실행 시간 제한을 초과하면, THEN THE Backend SHALL 작업을 중단하거나 부분 결과를 반환하고 초과 작업을 자동 실행하지 않는다.
