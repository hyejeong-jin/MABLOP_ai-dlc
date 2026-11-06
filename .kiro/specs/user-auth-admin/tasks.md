# Implementation Plan: user-auth-admin

## Overview

공유 Pre_Shared_Token을 사용자별 계정(가입 승인, HMAC 서명 Session_Token, 사용자별 S3 prefix), Bedrock 사용 Grant, 초안 기록(Draft_Index), 레거시 데이터 바인딩(`jhe8888`)으로 교체한다. 구현 언어는 기존 코드베이스를 따른다: 백엔드는 Python(Lambda, pytest, hypothesis, moto), 프론트엔드는 TypeScript(React, vitest). 새 AWS 서비스, Python 패키지, npm 패키지는 추가하지 않는다.

작업 순서는 다음과 같다. 백엔드 기반(errors, user_scope, s3store 확장, auth_messages/auth_log, auth_crypto, auth_store, auth_throttle) → auth_service → grant_service → draft_history → access_controller/handler → 기존 컴포넌트 userId 키 전환 → legacy_migrator → infra 파일 편집 → 프론트엔드 → 문서/runbook → 전체 테스트 1회 실행. 배포, push, IAM/SSM 적용, 프로덕션 `bootstrap-admin`, `jhe8888` 승인과 바인딩 실제 실행은 이 계획의 작업이 아니며 "사용자 확인 필요 항목" 섹션에 분리했다.

이 워크플로는 설계와 계획 산출물만 만든다. 구현은 별도 실행에서 한다. 초안 작성 화면의 제목/개요/메모 안내 문구는 이미 완료되었으므로 포함하지 않는다.

## Tasks

- [x] 1. 백엔드 기반: errors, user_scope, 메시지/로그, s3store 확장
  - [x] 1.1 `errors.py`에 `forbidden`(403), `not_found`(404) 추가
    - `STATUS` 매핑에 두 코드를 추가하고 `MablopError`, `error_body`, `response`, `error_response`는 바꾸지 않는다
    - 변경 테스트: `backend/tests/test_errors.py`에 매핑 두 행 추가 (기존 단언은 유지)
    - _Requirements: 16.5, 16.7, 17.7_

  - [x] 1.2 `user_scope.py`, `auth_messages.py`, `auth_log.py` 작성
    - `user_scope`: `ContextVar` 기반 `bind(user_id)` 컨텍스트 매니저(`^u_[0-9a-f]{16}$` 검증, 종료 시 reset)와 `current_user_id()`(미바인딩이면 `internal_error`). `s3store`와 분리해 `importlib.reload(s3store)` 영향을 받지 않게 한다
    - `auth_messages`: 요구사항과 설계의 한국어 메시지 상수를 UTF-8 리터럴로 한 모듈에 모은다 (로그인 실패, 승인 대기, 비활성, 로그인 차단, 가입 과다, 가입 마감, 관리자 필요, 로그인 필요, 내부 오류, Grant 필요, 이미 관리자 존재, 레거시 이미 연결됨, 입력 검증 메시지, 상태 전이 메시지, 404 메시지 등)
    - `auth_log`: `log(event, outcome, user_id=None)`가 `event`, `outcome`, (존재하는 사용자의) `userId` 세 필드만 JSON 한 줄로 기록
    - _Requirements: 3.5, 6.4, 6.5, 6.6, 16.5, 19.19_

  - [x] 1.3 `s3store.py` 확장 (기존 네 함수 시그니처는 유지)
    - 키 빌더: `USER_ID_PATTERN`, `user_key(relative, user_id=None)`, `auth_key(relative)`. userId 불일치는 `internal_error`, 상대 경로가 비었거나 `/`로 시작하거나 `..`/역슬래시/제어문자를 포함하면 `bad_request` "허용되지 않는 경로입니다."
    - `assert_own_image_keys(keys)`: 모든 키가 `users/{acting userId}/images/`로 시작하고 경로 규칙을 만족해야 하며, 아니면 `bad_request` "허용되지 않는 이미지 경로입니다." (객체를 읽기 전에 검사)
    - 헬퍼: `list_keys(prefix)`(페이지네이션, `lastModified` UTC ISO-8601 밀리초), `key_exists(key)`(정확한 키 `ListObjectsV2`), `read_json_optional(key)`(`(obj, etag)` 또는 `None`; `NoSuchKey`/404와 선택적 객체의 `AccessDenied`를 없음으로 처리), `list_user_ids()`, `head(key)`, `copy(src, dst)`, `delete(key)`, `put_json_if_absent(key, obj)`(`If-None-Match: *`), `update_json(key, mutate, default=None, retries=3)`(ETag 읽기 + `If-Match` 쓰기, `mutate`가 예외를 내면 아무것도 쓰지 않음)
    - `metadata/ratelimit.json`은 사용자 prefix 밖에 유지
    - _Requirements: 8.1, 8.2, 8.7, 8.9, 17.5, 19.1, 22.1, 22.5_

  - [x]* 1.4 키 빌더 속성 테스트 작성
    - 파일: `backend/tests/test_s3store_keys_property.py`
    - **Property 14: Key builder accepts exactly the safe inputs**
    - **Validates: Requirements 8.1, 8.2**

  - [x]* 1.5 s3store, user_scope, auth_messages 단위 테스트 작성
    - 파일: `backend/tests/test_s3store.py`(추가), `backend/tests/test_user_scope.py`, `backend/tests/test_auth_messages.py`
    - `update_json` 재시도/ETag/`mutate` 예외 시 무변경, `put_json_if_absent`, `read_json_optional`의 `AccessDenied` 처리, `list_keys` 페이지네이션, `key_exists`, 미바인딩 `user_scope` 예외
    - 모든 메시지 상수에 한글이 있고 `?` 연속이나 U+FFFD가 없으며, 응답 문자열이 요구사항의 리터럴과 같은지 확인 (상수가 아니라 리터럴과 비교)
    - _Requirements: 8.1, 8.2, 16.4, 16.5, 17.9_

- [x] 2. 인증 코어: auth_crypto, auth_store, auth_throttle
  - [x] 2.1 `auth_crypto.py` 작성
    - `hash_password`(PBKDF2-HMAC-SHA256, 600000회, `secrets` 16바이트 salt, `iterations` 인자는 테스트용), `verify_password`(저장된 `alg`/`iterations` 사용, 범위 밖이면 `internal_error`, `hmac.compare_digest`), `burn_verify`, `generate_temp_password`(16자, 혼동 문자 제외 알파벳)
    - `get_signing_secret`(SSM SecureString, 경로는 `MABLOP_SIGNING_SECRET_SSM_PATH`, 토큰 경로와 같거나 32자 미만이거나 읽기 실패면 `internal_error`, 성공만 웜 컨테이너에 캐시, `reset_cache()`)
    - `sign_token`/`verify_token`: `base64url(payload).base64url(HMAC-SHA256)`, 패딩 없음, 페이로드 키 `v`/`uid`/`iat`/`exp`(epoch ms), TTL 8시간, 길이 512 이하, 엄격한 base64url 디코더(정규 인코딩 재검증), 서명 검증 후 페이로드 파싱, 모든 실패는 동일한 `unauthorized`
    - `to_iso_ms`/`from_iso_ms`: 정수 연산으로 밀리초 변환
    - 표준 라이브러리만 사용
    - _Requirements: 2.2, 2.3, 2.5, 3.1, 3.2, 3.3, 3.6, 4.3, 7.1, 7.2, 7.3, 7.4_

  - [x] 2.2 `backend/tests/conftest.py` 공용 fixture 추가
    - 고정 테스트 userId를 `user_scope`에 바인딩하는 autouse fixture (기존 컴포넌트 테스트가 그대로 동작하도록)
    - 사용자 레코드와 Session_Token을 만드는 헬퍼, 서명 비밀(moto SSM)과 `auth_crypto.reset_cache()` fixture, 부트스트랩 토큰 fixture, S3 호출 키/순서 기록 및 실패 주입용 boto3 이벤트 훅
    - 테스트 본문이 아닌 fixture 변경이다
    - _Requirements: 17.7, 17.9_

  - [x]* 2.3 Session_Token 왕복/구조 속성 테스트 작성
    - 파일: `backend/tests/test_auth_crypto_property.py`
    - **Property 1: Session_Token round trip and structure**
    - **Validates: Requirements 2.2, 2.3, 4.3**

  - [x]* 2.4 변조/만료/외부 토큰 거부 속성 테스트 작성
    - 파일: `backend/tests/test_auth_token_tamper_property.py`
    - **Property 2: Altered, expired, or foreign tokens are rejected uniformly**
    - **Validates: Requirements 2.2, 4.3, 4.4**

  - [x]* 2.5 비밀번호 해시 왕복 속성 테스트 작성
    - 파일: `backend/tests/test_auth_password_hash_property.py`
    - **Property 3: Password hash round trip**
    - **Validates: Requirements 3.2**

  - [x]* 2.6 약하거나 없는 Signing_Secret은 실패로 닫힘 속성 테스트 작성
    - 파일: `backend/tests/test_auth_secret_property.py`
    - **Property 27: A weak or unavailable Signing_Secret fails closed**
    - **Validates: Requirements 7.3**

  - [x]* 2.7 auth_crypto 단위 테스트 작성
    - 파일: `backend/tests/test_auth_crypto.py`
    - 실제 기본값(알고리즘, 600000회, salt 16바이트), 서명 경로와 토큰 경로 동일 시 실패, 캐시 동작, 비정규 base64url 거부, 임시 비밀번호 알파벳과 길이
    - _Requirements: 3.1, 3.2, 3.3, 7.1, 7.2, 7.3, 17.9_

  - [x] 2.8 `auth_store.py` 작성
    - `get_user`, `find_user_id`, `put_user`, `update_user`(`update_json`), `claim_login_id`(`created`/`exists`/`healed`, 대상 계정이 없고 60초 이상 된 조회 항목은 덮어씀), `release_login_id`(여전히 해당 userId를 가리킬 때만 삭제), `delete_user`, `list_users`(`Delimiter="/"` 목록, `users/_auth/` 제외, `account.json` 없는 prefix 건너뜀), `admin_pointer`/`commit_admin_pointer`(create-once)
    - loginId 조회는 S3 읽기 1회로 userId를 돌려준다
    - _Requirements: 1.5, 1.6, 8.1, 9.2, 9.3, 11.1, 11.3_

  - [x]* 2.9 auth_store 단위 테스트 작성
    - 파일: `backend/tests/test_auth_store.py`
    - `claim_login_id` 세 결과(60초 규칙 포함), `release_login_id` 소유 확인, `list_users`의 `_auth` 제외, 관리자 포인터 create-once
    - _Requirements: 1.6, 9.3, 17.9_

  - [x] 2.10 `auth_throttle.py` 작성
    - 순수 규칙 함수와 `update_json` 기반 영속화: `check_login`, `record_failure`(형식이 올바른 loginId만 항목 생성, 전역 목록에는 항상 기록), `record_success`, `reset_login`, `guard_global`, `admit_register`
    - 상수: 로그인 5회/10분/차단 10분, 전역 30회/10분/차단 10분, 가입 5회/60분, 대기 계정 상한 10, 항목 최대 200
    - 쓰기마다 만료 항목 제거, 200 초과 시 `last`가 가장 오래된 항목부터 제거. 차단 중 시도는 차단 시간을 연장하지 않는다
    - _Requirements: 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.9, 5.10_

  - [x]* 2.11 스로틀 상태 유지/가지치기 속성 테스트 작성
    - 파일: `backend/tests/test_auth_throttle_property.py`
    - **Property 13: Throttle state stays bounded and pruned**
    - **Validates: Requirements 5.9**

  - [x]* 2.12 전역 실패 게이트와 가입 제한 속성 테스트 작성
    - 파일: `backend/tests/test_auth_throttle_global_property.py` (테스트 안에 15줄 내외 기준 모델 포함)
    - **Property 12: Global failure gate and registration limits follow the reference model**
    - **Validates: Requirements 5.6, 5.7, 5.8**

  - [x]* 2.13 auth_throttle 단위 테스트 작성
    - 파일: `backend/tests/test_auth_throttle.py`
    - 5회째 실패에서 차단, 차단 중 올바른 비밀번호도 거부, 성공 시 카운터 초기화, 잘못된 형식 loginId는 전역에만 집계
    - _Requirements: 5.2, 5.3, 5.4, 5.5, 17.9_

- [x] 3. Checkpoint - 백엔드 기반 범위 최소 검증
  - 이 범위에서 만든/바꾼 테스트 파일만 실행한다 (예: `pytest backend/tests/test_errors.py backend/tests/test_s3store.py` 등 해당 파일). 전체 스위트는 마지막 체크포인트(17)에서 한 번에 실행한다.
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. auth_service: 가입, 로그인, 계정 관리
  - [x] 4.1 `auth_service.py` 작성: 검증, `register`, `login`, `get-me`, `change-password`
    - 입력 검증: loginId `^[a-z0-9_-]{4,32}$`, name은 `strip()` 후 1~30자(제어문자 불가), password 10~128자(다른 규칙 없음). 오류 메시지는 필드 이름과 규칙을 한국어로 명시
    - `register`: 검증 → `admit_register` → 대기 계정 10개 이상이면 429 → `hash_password`(항상) → `claim_login_id` → 신규일 때만 `put_user`(role `member`, status `pending`, `approvedAt` null, `credentialsChangedAt`=`createdAt`) → 존재 여부와 무관하게 동일한 성공 메시지. `jhe8888`도 특별 처리 없음
    - `login`: 형식/타입 오류는 실패 경로 → 차단 확인(해싱 전) → 조회 → 없으면 `burn_verify`, 있으면 `verify_password` → 실패는 `record_failure` + 동일한 401 → 비밀번호 일치 시 `pending` 403 / `disabled` 403 / `active`는 `record_success` 후 토큰, `expiresAt`, `name`/`loginId`/`role` 반환
    - `get-me`: `ctx.user`에서 추가 읽기 없이 반환
    - `change-password`: 차단 확인 → 새 비밀번호 정책/동일 여부 → 현재 비밀번호 검증(실패는 `record_failure` + `bad_request` "현재 비밀번호가 올바르지 않습니다.", 401 아님) → 새 해시 → `credentialsChangedAt` 갱신 → 같은 `now_ms`로 새 토큰 발급
    - 응답에는 해시 파라미터, salt, 비밀을 넣지 않는다. 예기치 않은 예외 처리는 handler(7.2)에서 한다
    - 각 이벤트는 `auth_log`로 기록 (event/outcome/userId만)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 3.1, 3.4, 3.5, 5.2, 5.3, 5.4, 5.5, 5.7, 5.8, 5.10, 6.1, 6.2, 6.3, 6.4, 6.5, 9.15, 12.1, 12.2, 12.3, 12.4, 12.5_

  - [x] 4.2 `auth_service.py` 작성: `bootstrap-admin`, 관리자 계정 액션, `migrate-legacy-data` 뼈대
    - `bootstrap-admin`: 관리자 포인터가 있으면 입력을 읽기 전에 403 "이미 관리자 계정이 있습니다." → 검증 → 해시 → loginId 선점(이미 존재하면 role `admin` 계정의 중단된 실행만 재개) → role `admin`/status `active` 계정 기록 → 포인터 `commit`(경쟁에서 지면 새 계정과 조회 항목 삭제 후 403). Legacy_Migrator를 호출하지 않고 `account.json` 외에는 아무것도 쓰지 않는다
    - `admin-list-users`(7개 필드, `pending` 우선, 이후 `createdAt` 오름차순), `admin-approve-user`(`pending`만, `active`와 `approvedAt` 설정, 이 단계에서는 모든 대상이 `legacyBinding` `not_applicable` 구조를 반환하고 `jhe8888` 연결은 10.2에서 완성), `admin-reject-user`(`pending`만, 조회 항목 먼저 삭제 후 레코드 삭제), `admin-disable-user`(`active` 멤버만, `credentialsChangedAt` 갱신), `admin-enable-user`(`disabled` 멤버만), `admin-reset-password`(임시 비밀번호는 해시만 저장, `credentialsChangedAt` 갱신, `reset_login`, 평문은 응답에 한 번만)
    - 대상 확인 순서: 형식 불일치/레코드 없음 → 404 "대상 사용자를 찾을 수 없습니다.", role `admin` → `bad_request` "관리자 계정은 변경할 수 없습니다.", 상태 확인은 `update_user` 변경 함수 안에서 하고 위반 시 필요한 상태를 한국어로 명시하며 레코드는 변경하지 않는다
    - `migrate-legacy-data`: 관리자 전용 함수의 대상 해석(선택 `targetUserId`, 없으면 `find_user_id("jhe8888")`, 형식 불일치/레코드 없음은 404)까지 작성하고, 실제 복사는 `legacy_migrator`를 호출 시점에 가져와(lazy import) 실행하도록 둔다. 모듈 자체는 10.1에서 만들고 10.2에서 호출부를 마무리한다
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.12, 9.13, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 11.9, 11.10_

  - [x]* 4.3 자격 증명 검증 규칙 속성 테스트 작성
    - 파일: `backend/tests/test_auth_validation_property.py`
    - **Property 4: Credential field validation matches the rules exactly**
    - **Validates: Requirements 1.4, 3.4, 12.4**

  - [x]* 4.4 가입 멱등성과 계정 비노출 속성 테스트 작성
    - 파일: `backend/tests/test_auth_register_property.py`
    - **Property 5: Registration is idempotent per loginId and does not reveal existing accounts**
    - **Validates: Requirements 1.1, 1.3, 1.5, 6.2, 9.15**

  - [x]* 4.5 로그인 실패 구별 불가 속성 테스트 작성
    - 파일: `backend/tests/test_auth_login_failure_property.py` (`hashlib.pbkdf2_hmac` 호출 횟수로 확인)
    - **Property 6: Failed logins are indistinguishable**
    - **Validates: Requirements 2.4, 2.5, 6.1**

  - [x]* 4.6 계정 상태별 로그인 결과 속성 테스트 작성
    - 파일: `backend/tests/test_auth_login_status_property.py`
    - **Property 7: Login outcome follows account status**
    - **Validates: Requirements 2.1, 2.6, 2.7**

  - [x]* 4.7 loginId별 스로틀 기준 모델 속성 테스트 작성
    - 파일: `backend/tests/test_auth_throttle_model_property.py` (로그인과 `change-password` 실패를 함께 시뮬레이션)
    - **Property 11: Per-loginId throttle follows the reference model**
    - **Validates: Requirements 5.2, 5.3, 5.4, 5.5, 5.10**

  - [x]* 4.8 부트스트랩 속성 테스트 작성 (사용자 핵심 속성: 빈 작업공간)
    - 파일: `backend/tests/test_bootstrap_property.py` (버킷에 레거시 객체를 미리 둔다)
    - **Property 19: Bootstrap creates at most one administrator with an empty workspace**
    - **Validates: Requirements 9.2, 9.3, 9.4**

  - [x]* 4.9 관리자 액션 상태 머신 속성 테스트 작성
    - 파일: `backend/tests/test_auth_admin_state_property.py`
    - **Property 20: Administrator actions follow the status state machine**
    - **Validates: Requirements 11.2, 11.3, 11.4, 11.5, 11.7, 11.8, 11.9**

  - [x]* 4.10 사용자 목록 속성 테스트 작성
    - 파일: `backend/tests/test_auth_admin_list_property.py`
    - **Property 21: User listing is complete, ordered, and free of secrets**
    - **Validates: Requirements 6.3, 11.1**

  - [x]* 4.11 임시 비밀번호 속성 테스트 작성
    - 파일: `backend/tests/test_auth_temp_password_property.py`
    - **Property 23: Temporary passwords**
    - **Validates: Requirements 3.5, 11.6**

  - [x]* 4.12 비밀번호 변경 속성 테스트 작성
    - 파일: `backend/tests/test_auth_change_password_property.py`
    - **Property 24: Password change**
    - **Validates: Requirements 5.10, 12.2, 12.3, 12.4, 12.5**

  - [x]* 4.13 auth_service 단위 테스트 작성
    - 파일: `backend/tests/test_auth_service.py`
    - 응답 메시지가 요구사항의 한국어 리터럴과 일치, 토큰 수명 8시간과 갱신 액션 없음, 대기 계정 10개 마감, 부트스트랩 이후 `get-learning-status` 0/0, 부트스트랩 중단 후 재개, 승인된 `jhe8888` 외 계정의 `legacyBinding` `not_applicable`
    - _Requirements: 1.2, 2.2, 5.8, 9.4, 9.18, 17.9_

- [x] 5. grant_service: Bedrock 사용 Grant
  - [x] 5.1 `grant_service.py` 작성
    - `request`, `list_mine`, `admin_list`, `admin_approve`, `admin_reject`, `admin_revoke`, `verify(user_id, grant_id, now_ms)`, `is_expired`(D18: `expiresAt <= now`이면 만료)
    - 모든 쓰기는 `update_json`으로 `users/{userId}/metadata/grants.json`에 하고, 선행 조건 검사(요청 1건, 20개 상한과 가지치기, 필요한 상태)는 변경 함수 안에서 수행해 실패 시 객체가 그대로 남게 한다
    - `grantId`는 `"g_" + secrets.token_hex(8)` (충돌 시 재생성). `purpose`는 없음/null/100자 이하 문자열만 허용(공백 문자열은 null). `expiresInDays`는 7/30/90(기본 30)만 허용하고 불리언/실수/문자열은 거부, 저장소 읽기 전에 검증. `verify`는 `^g_[0-9a-f]{16}$` 불일치면 S3 읽기 없이 `False`, 아니면 `read_json_optional` 1회
    - 관리자 액션의 대상 사용자는 `auth_store`로 해석(없으면 404 "대상 사용자를 찾을 수 없습니다."), 없는 Grant는 404 "대상 권한을 찾을 수 없습니다."
    - 저장된 Grant에는 비밀 값이 없고 `expired` 필드는 저장하지 않는다
    - _Requirements: 19.1, 19.2, 19.3, 19.4, 19.5, 19.6, 19.7, 19.8, 19.9, 19.10, 19.11, 19.12, 19.13, 19.14, 19.15, 19.17, 19.18, 19.19, 20.2, 20.3_

  - [x]* 5.2 Grant 상태 전이 속성 테스트 작성
    - 파일: `backend/tests/test_grant_transitions_property.py`
    - **Property 30: Grant transitions follow the status state machine**
    - **Validates: Requirements 19.8, 19.9, 19.10, 19.11, 19.12, 19.13, 19.14, 19.15**

  - [x]* 5.3 Grant 생성 제한 속성 테스트 작성
    - 파일: `backend/tests/test_grant_creation_property.py`
    - **Property 31: Grant creation respects the per-user limits**
    - **Validates: Requirements 19.1, 19.2, 19.3, 19.4, 19.5, 19.17, 19.18**

  - [x]* 5.4 Grant 목록 범위/정렬/만료 표시 속성 테스트 작성
    - 파일: `backend/tests/test_grant_listing_property.py`
    - **Property 32: Grant listings are scoped, ordered, and flag expiry**
    - **Validates: Requirements 19.6, 19.7**

  - [x]* 5.5 grant_service 단위 테스트 작성
    - 파일: `backend/tests/test_grant_service.py`
    - 한국어 메시지 리터럴, 빈 purpose는 null, `expiresAt == now` 경계, 404 두 종류, 만료 Grant도 폐기 가능, 응답에 비밀/불필요 필드 없음
    - _Requirements: 19.10, 19.14, 19.15, 19.19, 17.9_

- [x] 6. draft_history: 초안 기록
  - [x] 6.1 `draft_history.py` 작성
    - `derive_title`(요청 제목 → 본문 첫 `#` 줄 → "제목 없음"), `valid_draft_id`(비어 있거나 `/`, `\`, `..`, 제어문자 포함이면 거짓), `record`(`update_json`, 같은 `draftId` 제거 후 추가, UTC ISO-8601 밀리초 `createdAt`)
    - `list_drafts`: 인덱스 1회 읽기 → `generated-posts/` 목록 → 인덱스에 없는 객체를 본문 제목과 `LastModified`로 보충(변경 함수 안에서 중복 재확인) → `createdAt` 내림차순(동률은 `draftId` 내림차순) 최대 100개
    - `delete_draft`: 유효성 → 인덱스/`key_exists` 둘 다 없으면 404 → 인덱스 항목 먼저 제거 → 객체 삭제(실패는 예외로 전파, handler가 `internal_error`로 매핑) → `{draftId, deleted: true}`
    - 두 액션 모두 `user_scope`로 현재 사용자 prefix만 사용
    - _Requirements: 22.1, 22.2, 22.3, 22.5, 22.6, 22.7, 22.8, 22.9, 22.10, 22.11, 22.13, 22.14_

  - [x]* 6.2 제목 도출 속성 테스트 작성
    - 파일: `backend/tests/test_draft_title_property.py`
    - **Property 34: Draft title derivation**
    - **Validates: Requirements 22.2**

  - [x]* 6.3 `list-drafts` 정렬/상한 속성 테스트 작성
    - 파일: `backend/tests/test_draft_list_property.py`
    - **Property 36: `list-drafts` is ordered and capped**
    - **Validates: Requirements 22.7**

  - [x]* 6.4 초안 인덱스 정합성 속성 테스트 작성 (사용자 핵심 속성: 초안 인덱스 정합성)
    - 파일: `backend/tests/test_draft_index_property.py` (인덱스 쓰기 실패 주입, 인덱스에 없는 객체 포함)
    - **Property 35: Draft index stays consistent with the stored drafts**
    - **Validates: Requirements 22.1, 22.3, 22.4, 22.6**

  - [x]* 6.5 `delete-draft` 속성 테스트 작성
    - 파일: `backend/tests/test_draft_delete_property.py` (S3 쓰기 순서 기록, 객체 삭제 실패 주입)
    - **Property 37: `delete-draft` validates, orders its steps, and recovers**
    - **Validates: Requirements 22.8, 22.9, 22.10, 22.11**

  - [x]* 6.6 draft_history 단위 테스트 작성
    - 파일: `backend/tests/test_draft_history.py`
    - `list-drafts`가 인덱스를 1회만 읽음, 보충 후 두 번째 호출은 쓰기 없음, 중단된 삭제 후 재보충, 한국어 메시지 리터럴
    - _Requirements: 22.5, 22.6, 22.10, 17.9_

- [x] 7. access_controller와 handler: 인증 파이프라인, Grant 훅, DISPATCH 규약
  - [x] 7.1 `access_controller.py` 변경
    - 상수: `AUTH_EXEMPT_ACTIONS`(정확히 `login`, `register`, `bootstrap-admin`), `ADMIN_ONLY_ACTIONS`(11개), `BEDROCK_ACTIONS`(5개), `RequestContext(action, user_id, role, user)`
    - `check_request(headers, body, now=None)`: `check_body_length` → `check_rate` → 관대한 JSON 파서로 `action` 확인 → `login`/`register`는 사용자 없는 컨텍스트 → `bootstrap-admin`은 `guard_global` 후 `check_token`(실패는 `record_failure(None)` 후 `unauthorized`) → 그 외(알 수 없는 액션 포함)는 `X-Mablop-Token`을 `verify_token`하고 `get_user` 정확히 1회, `active`와 `iat >= credentialsChangedAt` 확인, 모든 실패는 같은 401 → 관리자 전용 액션은 role 확인 후 403 "관리자 권한이 필요합니다." → S3 장애 등 예외는 `internal_error`
    - `requires_grant`(순수 판정: role `member`이고 `BEDROCK_ACTIONS`이며 `get-result`의 `kind=="draft"`가 아님), `check_grant`(본문의 `grantId`만 사용, `grant_service.verify`, 실패 시 모든 원인에 동일한 `forbidden` 메시지, 감사 로그 `grant`/`blocked`)
    - `check_token`, `check_body_length`, `check_rate`, `RATELIMIT_KEY`는 그대로 둔다
    - 변경 테스트: `backend/tests/test_access_controller.py`의 `test_check_request_happy_path`를 Session_Token으로 전환 (토큰/속도/본문 길이 테스트는 유지)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 5.1, 5.6, 6.6, 10.1, 10.5, 11.10, 17.1, 17.2, 17.7, 18.2, 19.16, 20.1, 20.2, 20.3, 20.4, 20.5, 20.6, 20.7, 20.8, 20.9, 20.10_

  - [x] 7.2 `handler.py` 변경
    - `DISPATCH` 값 규약을 `(body, ctx)`로 바꾸고 기존 8개 항목은 `lambda body, ctx: <기존 호출>`로 감싼다. 새 항목 20개 추가: 계정 12개(`login`, `register`, `bootstrap-admin`, `get-me`, `change-password`, `migrate-legacy-data`, `admin-list-users`, `admin-approve-user`, `admin-reject-user`, `admin-disable-user`, `admin-enable-user`, `admin-reset-password`), Grant 6개, `list-drafts`, `delete-draft`. 갱신/refresh 액션은 없다
    - 본문 파싱과 액션 조회(알 수 없는 액션은 `bad_request`) 후 `access_controller.check_grant(ctx, body)`를 호출하고, `ctx.user_id`가 있으면 `user_scope.bind` 안에서 실행한다
    - `AUTH_SCOPE_ACTIONS`(새 액션 20개)와 `check_request` 예외에 대한 `internal_error` + 일반 메시지 매핑, 감사 로그에는 예외 클래스 이름만 기록. 기존 8개 액션의 오류 동작은 유지
    - 변경 테스트: `backend/tests/test_handler.py`의 fixture와 `_event`를 Session_Token으로 전환(기존 Bedrock 액션 호출은 `admin` 토큰으로 실행해 Grant 면제), `test_missing_token_returns_401`에 한국어 메시지 단언 추가
    - _Requirements: 4.4, 4.5, 6.6, 17.3, 17.4, 17.7, 17.8, 20.1_

  - [x] 7.3 `backend/pyproject.toml`의 `py-modules`에 새 모듈 10개 추가
    - `user_scope`, `auth_messages`, `auth_log`, `auth_crypto`, `auth_store`, `auth_throttle`, `auth_service`, `grant_service`, `draft_history`, `legacy_migrator`. `backend/requirements.txt`는 건드리지 않는다
    - `legacy_migrator`는 10.1에서 만들어지므로 이 항목은 10.1 이후에 수행한다 (의존 그래프의 wave 13)
    - _Requirements: 17.10, 18.1_

  - [x]* 7.4 인증 게이트 속성 테스트 작성
    - 파일: `backend/tests/test_access_controller_auth_property.py`
    - **Property 8: Authentication gate**
    - **Validates: Requirements 4.1, 4.3, 4.4, 4.5, 10.1**

  - [x]* 7.5 관리자 전용 액션 속성 테스트 작성
    - 파일: `backend/tests/test_admin_gate_property.py`
    - **Property 10: Administrator actions require the admin role**
    - **Validates: Requirements 4.8, 19.16**

  - [x]* 7.6 자격 증명 변경 시 이전 토큰 무효화 속성 테스트 작성
    - 파일: `backend/tests/test_token_invalidation_property.py`
    - **Property 22: Credential changes invalidate earlier tokens**
    - **Validates: Requirements 11.10, 12.2**

  - [x]* 7.7 Grant 검증 게이트 속성 테스트 작성 (사용자 핵심 속성: Grant 검증)
    - 파일: `backend/tests/test_grant_gate_property.py` (`grants.json` GET 횟수 기록, 요청 사이 폐기)
    - **Property 29: Grant verification gate**
    - **Validates: Requirements 20.1, 20.2, 20.3, 20.4, 20.5, 20.6, 20.8, 20.9, 20.10**

  - [x]* 7.8 비밀/개인정보 비노출 속성 테스트 작성
    - 파일: `backend/tests/test_auth_secret_leak_property.py`
    - **Property 25: Secrets and personal data never leak**
    - **Validates: Requirements 3.5, 6.3, 6.4, 6.5, 7.4**

  - [x]* 7.9 예기치 않은 실패의 일반 메시지 속성 테스트 작성
    - 파일: `backend/tests/test_auth_generic_failure_property.py`
    - **Property 26: Unexpected failures return a generic message**
    - **Validates: Requirements 6.6**

  - [x]* 7.10 access_controller 인증 단위 테스트 작성
    - 파일: `backend/tests/test_access_controller_auth.py`
    - 면제 집합이 정확히 3개, 검사 순서(본문 길이 → 속도 → 401), `bootstrap-admin`의 토큰 검사가 필드 검증보다 먼저, 알 수 없는 액션 + 토큰 없음은 401, 인증 요청당 `account.json` GET 1회, 관리자 없는 빈 버킷에서 면제 액션만 성공, 부트스트랩 토큰으로 다른 액션 호출 시 401
    - _Requirements: 4.1, 4.2, 4.5, 9.1, 10.1, 10.5, 18.2, 17.9_

  - [x]* 7.11 handler 인증 단위 테스트 작성
    - 파일: `backend/tests/test_handler_auth.py`
    - `DISPATCH`에 새 액션 20개, `AUTH_SCOPE_ACTIONS`가 정확히 그 20개, `ADMIN_ONLY_ACTIONS` 11개, `BEDROCK_ACTIONS` 5개, refresh 액션 없음, 헤더/쿼리의 `grantId`는 무시되어 403, `errors`의 `forbidden`/`not_found` 매핑
    - _Requirements: 17.4, 20.7, 16.7, 17.9_

  - [x]* 7.12 Grant 훅 단위 테스트 작성
    - 파일: `backend/tests/test_grant_gate.py`
    - Bedrock 액션당 `grants.json` GET 1회, `get-result`(`kind=draft`)와 비-Bedrock 액션은 GET 0회, 관리자 면제, 폐기 직후 다음 호출 거부, 한국어 메시지 리터럴
    - _Requirements: 20.1, 20.5, 20.8, 20.9, 20.10, 17.9_

- [x] 8. Checkpoint - 인증/Grant/handler 범위 최소 검증
  - 이 범위에서 만든/바꾼 테스트 파일만 실행한다 (예: `test_auth_*`, `test_grant_*`, `test_draft_*`, `test_access_controller*`, `test_handler*`). 전체 스위트는 마지막 체크포인트(17)에서 한 번에 실행한다.
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. 기존 컴포넌트 userId 키 전환
  - [x] 9.1 `index_store.py`, `style_profile.py`, `checkpoint.py` 키 전환
    - `INDEX_KEY`, `MAP_KEY`, `MANIFEST_KEY`, 프로필 키, 체크포인트 키를 `s3store.user_key(...)`로 만든다. `checkpoint_key(run_id)`는 `user_key(f"metadata/learn-checkpoint-{run_id}.json")`이고 `..`를 포함한 `resumeRunId`는 빌더가 거부한다
    - 변경 테스트: `backend/tests/test_index_store.py`(상대 상수를 `user_key(상수)`로 읽기), `backend/tests/test_style_profile.py`(`style-profile/profile.json` 단언을 사용자 키로), 필요하면 `test_checkpoint.py`, `test_checkpoint_property.py`. 단언 강도는 유지
    - _Requirements: 8.3, 17.5, 17.7, 17.8_

  - [x] 9.2 `style_learner.py` 키 전환
    - `_store_raw`, `_load_raw`, 학습 상태 읽기/쓰기를 `user_key`로 전환. `rebuild_from_stored`는 직접 boto3 paginator 대신 `s3store.list_keys(user_key("raw-posts/"))`를 사용하고 전체 prefix를 떼어 postId를 얻는다. `get_learning_status`는 파일이 없으면 `{"processedPosts": 0, "chunks": 0, "posts": []}`. 응답의 `indexKey`/`styleProfileKey`는 전체 키(D12)
    - 변경 테스트: `backend/tests/test_style_learner_flow.py`의 raw-post/프로필/인덱스/체크포인트 키 단언. 레거시 키 문자열을 단언하는 다른 테스트(`test_style_learner_property.py`, `test_relearn_property.py` 등)가 깨지면 Req 17.7 범위 안에서만 수정
    - _Requirements: 8.3, 8.8, 17.5, 17.7, 17.8_

  - [x] 9.3 `style_learner.rebuild_from_stored`의 깨진 한글 오류 메시지 수정
    - 현재 `"??? raw-posts? ????."`로 깨진 메시지를 UTF-8 한국어 리터럴로 고친다 (예: "저장된 raw-posts가 없습니다. 먼저 스타일 학습을 진행하세요."). 신규 멤버는 글이 없어 이 메시지를 볼 가능성이 높다
    - 메시지는 `auth_messages`가 아니라 기존 모듈의 리터럴로 유지해도 되고, 어느 쪽이든 테스트는 리터럴 문자열과 비교한다. `test_style_learner_flow.py`에 빈 raw-posts에서 `rebuild-from-stored`가 이 메시지를 내는 케이스를 추가
    - 같은 파일(`style_learner.py`)을 쓰는 9.2 다음에 수행한다
    - _Requirements: 16.4, 17.7, 8.8_

  - [x] 9.4 `image_analyzer.py` 키 전환
    - `issue_presigned_urls`는 `user_key(f"images/{uuid}.{ext}")` 키로 서명하고, `analyze_images`는 시작 시 `assert_own_image_keys`를 호출한다. 캡션 JSON 키는 검증된 이미지 키에서 파생해 사용자 prefix 안에 둔다
    - 변경 테스트: `backend/tests/test_image_analyzer_presigned.py`(`images/` prefix 단언 → `users/{id}/images/`), `backend/tests/test_image_analyzer_caption.py`(키 입력과 단언)
    - _Requirements: 8.6, 8.7, 17.5, 17.7, 17.8_

  - [x] 9.5 `drafting_engine.py` 키 전환과 초안 기록
    - `generate_from_prompt`에 선택 인자 `title` 추가. 초안을 `user_key("generated-posts/{id}.md")`에 저장하고 그 키를 `draftKey`로 반환한 뒤 `draft_history.record`를 `try/except Exception`으로 호출한다 (인덱스 실패는 예외 클래스 이름만 로그하고 초안은 정상 반환). `generate_draft`는 요청 `title`을 전달하고 시작 시 `assert_own_image_keys` 호출. `_load_profile`은 사용자 프로필, `get_result`는 `user_key("generated-posts/{id}.md")`만 읽는다(다른 사용자의 id는 없는 초안처럼 동작). `_retrieve_chunks`는 인덱스가 없으면 `bad_request` "학습된 글이 없습니다. 먼저 스타일 학습을 진행하세요."
    - 변경 테스트: `backend/tests/test_drafting_engine_flow.py`(레거시 키 문자열, `images/...` 입력 키, `draftKey`, handler 토큰, `users/{id}/metadata/drafts.json`에 새 `draftId`가 있다는 단언 추가, `grantId` 없는 멤버 호출 케이스 추가), `backend/tests/test_drafting_engine_generate.py`(`generated-posts/...` 키 단언, `s3store` 스텁에 `update_json` 추가, 요청 제목이 기록되는 케이스 추가)
    - _Requirements: 8.4, 8.5, 8.7, 17.5, 17.7, 17.8, 22.1, 22.2, 22.3, 22.4, 22.12_

  - [x] 9.6 이미지 marker 파일명 확인과 원본 파일명 보존
    - 현재 `generate_draft`는 `image_names[index] = key.rsplit("/", 1)[-1]`로 마커 `![img-N](파일명)`을 만든다. 업로드 키가 `users/{id}/images/<imgId>.<ext>`일 때 마커 파일명이 (a) 전체 키가 아니라 파일명만이고 (b) 사용자가 올린 원본 파일명인지 확인한다
    - 현재 구현은 `<imgId>.<ext>`(uuid 기반)이므로 원본 파일명이 아니다. 요구가 원본 파일명 표시라면, 경로 안전성(`..`, `/`, 제어문자 제거)을 지키면서 원본 파일명을 보존한다. 방법은 구현 시 기존 테스트와 맞춰 결정한다 (예: `issue-presigned-urls` 요청의 `filename`을 정규화해 이미지 메타(`images/<imgId>.json`)에 저장하고 마커 생성 때 읽기, 또는 파일명 부분을 키에 포함). 키 접두 검증(`assert_own_image_keys`)과 `user_key` 규칙은 그대로 통과해야 한다
    - 확인 결과와 선택한 방법을 테스트로 고정한다: 마커는 사용자 prefix를 노출하지 않고 파일명만 보이며, 원본 파일명이 보존되고, 파일명에 경로 구분자가 있어도 안전하다
    - `drafting_engine.py`/`image_analyzer.py`를 쓰는 9.4, 9.5 다음에 수행하며, 필요한 테스트 수정은 `test_drafting_engine_generate.py`, `test_image_analyzer_presigned.py`, `test_image_analyzer_caption.py` 범위에서만 한다
    - _Requirements: 8.6, 8.7, 17.3, 17.7_

  - [x] 9.7 `preview-learning-sources` 백엔드 테스트 추가
    - 파일: `backend/tests/test_preview_learning_sources.py` (현재 이 액션의 백엔드 테스트가 없다)
    - 공개 RSS에서 찾은 글만 미리보기하고 S3 쓰기 없음, Bedrock 호출 없음(`bedrock` 스텁으로 호출 0회 확인), `grantId` 없이 멤버가 성공(Bedrock 액션이 아니므로 Grant 훅 대상 아님), 세션 토큰 없으면 401, 잘못된 입력은 `bad_request`, 응답 형태는 기존과 동일
    - 크롤러/네트워크는 스텁으로 대체하고 실제 외부 요청은 하지 않는다
    - _Requirements: 17.3, 17.9, 20.9_

  - [x]* 9.8 사용자 신원은 토큰에서만 온다 속성 테스트 작성
    - 파일: `backend/tests/test_identity_from_token_property.py`
    - **Property 9: Identity comes only from the token**
    - **Validates: Requirements 4.7**

  - [x]* 9.9 사용자 데이터 격리 속성 테스트 작성 (사용자 핵심 속성: 사용자 격리)
    - 파일: `backend/tests/test_user_isolation_property.py` (S3 호출 키 기록으로 A의 액션이 `users/{A}/` 밖을 건드리지 않음을 확인, B의 객체 불변, B의 `get-result`/`delete-draft`는 not found)
    - **Property 15: User data isolation and namespace**
    - **Validates: Requirements 8.3, 8.4, 8.5, 8.6, 8.8, 8.9, 8.10, 9.4, 22.12, 22.13**

  - [x]* 9.10 이미지 키 소유권 속성 테스트 작성
    - 파일: `backend/tests/test_image_keys_property.py`
    - **Property 16: Image keys must belong to the acting user**
    - **Validates: Requirements 8.7**

  - [x]* 9.11 신규 멤버 빈 작업공간 단위 테스트 작성
    - 파일: `backend/tests/test_empty_workspace.py`
    - 새 사용자의 `get-learning-status` 0/0/빈 목록, 학습 전 `generate-draft`의 한국어 `bad_request`, 다른 사용자 초안 id의 `get-result` 실패
    - _Requirements: 8.5, 8.8, 17.9_

- [x] 10. legacy_migrator: 승인 시 `jhe8888` 바인딩
  - [x] 10.1 `legacy_migrator.py` 작성
    - `LEGACY_OWNER_LOGIN_ID = "jhe8888"`, `LEGACY_SINGLE_KEYS`(5개), 목록 대상(`raw-posts/*.txt`, `generated-posts/*.md`), `BINDING_KEY = "users/_auth/legacy-owner.json"`, `NOT_APPLICABLE`
    - `run(target_user_id, now=None)`: ① 바인딩 레코드를 `put_json_if_absent`로 만들고, 같은 userId면 진행, 다른 userId면 복사 없이 `already_bound`(메시지 "레거시 데이터는 이미 다른 사용자에게 연결되어 있습니다.", `migration.json` 쓰기 없음) ② 소스 목록(`list_keys`, 단일 키는 `head`, 404/403은 `absent`) ③ 대상 목록은 `list_keys`로만 확인(`head`로 AccessDenied를 없음으로 오인하지 않음) ④ 대상이 없을 때만 `copy`(실패는 `failedKeys`에 추가하고 계속), 같은 크기는 skipped, 다른 크기는 `divergedKeys`로 두고 덮어쓰지 않음 ⑤ 검증(복사된 객체 크기, 모든 소스 `raw-posts/*.txt`/`generated-posts/*.md`의 대상 존재, 복사/건너뛴 `learning-status.json`의 JSON 동일성) ⑥ 결과를 `users/{target}/metadata/migration.json`에 `put_json`(쓰기 실패는 `failedKeys`에 `metadata/migration.json` 추가, status `failed`) ⑦ `summarize`로 `legacyBinding` 객체(`status`, `copied`, `skipped`, `failedKeys`, `divergedKeys`, `message`) 생성
    - 이 모듈은 삭제 함수를 import하지 않고, 원본에 대한 삭제/덮어쓰기 요청을 만들지 않으며, 체크포인트, `images/*`, `metadata/ratelimit.json`은 복사하지 않는다. Draft_Index는 쓰지 않는다
    - _Requirements: 9.5, 9.6, 9.7, 9.8, 9.9, 9.11, 9.13, 9.16, 9.17, 9.18, 9.20, 8.10_

  - [x] 10.2 `auth_service`에 레거시 바인딩 연결
    - `admin_approve_user`: `active`로 바꾼 직후, 대상 loginId가 `LEGACY_OWNER_LOGIN_ID`일 때만 `legacy_migrator.run`을 한 번 호출하고 예외는 `failed`로 변환(예외 텍스트는 응답에 넣지 않음). 그 외는 `not_applicable`. 승인은 되돌리지 않는다. 응답에 `legacyBinding`을 포함
    - `migrate_legacy_data`: 호출부를 마무리한다. `already_bound`는 HTTP 403 "레거시 데이터는 이미 다른 사용자에게 연결되어 있습니다.", 그 외(`failed` 포함)는 200 `{"migration": {...}}`. 대상 사용자의 role/status는 제한하지 않는다(바인딩 레코드가 보호 장치)
    - 감사 로그 이벤트 `legacy-bind`
    - _Requirements: 9.10, 9.11, 9.12, 9.14, 9.16, 9.17, 9.18, 9.19, 22.14_

  - [x]* 10.3 복사 전용/멱등 마이그레이션 속성 테스트 작성 (사용자 핵심 속성: 마이그레이션 멱등성)
    - 파일: `backend/tests/test_legacy_migrator_property.py` (원본 불변, 삭제 요청 없음, 다른 크기 대상 불변, 두 번째 실행 후 상태 동일)
    - **Property 17: Migration copies only Legacy_Keys and Legacy_Drafts, never changes originals or divergent destinations, and is idempotent**
    - **Validates: Requirements 9.5, 9.6, 9.7, 9.8, 9.20**

  - [x]* 10.4 마이그레이션 검증/실패 보고 속성 테스트 작성
    - 파일: `backend/tests/test_legacy_failure_property.py` (`CopyObject` 실패 주입)
    - **Property 18: Migration verification and failure reporting**
    - **Validates: Requirements 9.9, 9.10, 9.11, 9.19**

  - [x]* 10.5 레거시 바인딩 1회성/멱등 속성 테스트 작성
    - 파일: `backend/tests/test_legacy_binding_property.py`
    - **Property 33: Legacy binding happens once and is idempotent**
    - **Validates: Requirements 9.12, 9.14, 9.16, 9.17, 9.18, 9.19**

  - [x]* 10.6 legacy_migrator 단위 테스트 작성
    - 파일: `backend/tests/test_legacy_migrator.py`
    - `LEGACY_OWNER_LOGIN_ID == "jhe8888"`, 모듈이 삭제 함수를 import하지 않음, 9개 글/31 청크 예시로 승인 후 `get-learning-status` 9/31, 승인 후 `list-drafts`가 복사된 초안을 보충, 기본 대상 해석과 알 수 없는 대상 404, `bootstrap-admin`이 마이그레이터를 호출하지 않음, 한국어 메시지 리터럴
    - _Requirements: 9.4, 9.10, 9.13, 9.17, 17.9, 22.14_

- [x] 11. Checkpoint - 백엔드 컴포넌트/레거시 범위 최소 검증
  - 9, 10에서 만든/바꾼 테스트 파일만 실행한다 (예: `test_style_*`, `test_index_store*`, `test_image_analyzer*`, `test_drafting_engine*`, `test_preview_learning_sources.py`, `test_legacy_*`). 전체 스위트는 마지막 체크포인트(17)에서 한 번에 실행한다.
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. infra: 템플릿과 IAM 파일 편집 (파일 편집만, 적용은 사용자 확인 후)
  - [x] 12.1 `infra/template.yaml` 편집
    - 파라미터 `MablopSigningSecretSsmPath`(String, 기본 `/mablop/signing-secret`)와 환경 변수 `MABLOP_SIGNING_SECRET_SSM_PATH`. `MABLOP_TOKEN_SSM_PATH`는 유지
    - `S3Prefixes`에 `users/*` 추가(레거시 8개 prefix 유지, 액션은 Get/Put 그대로), `ListRawPosts` 문장(Sid 유지)의 `s3:prefix` 조건에 `users/*`, `generated-posts/*` 추가, `s3:DeleteObject` 문장 신설(`users/_auth/login/*`, `users/*/account.json`, `users/*/generated-posts/*`만), `ReadToken` 문장에 서명 비밀 파라미터 ARN 추가
    - Function URL CORS, 버킷 CORS, 결제 알람, 리소스 유형은 바꾸지 않는다. 템플릿에 비밀 값을 넣지 않는다
    - 이 편집은 파일 변경일 뿐이며 적용(배포)은 "사용자 확인 필요 항목"이다
    - _Requirements: 7.1, 7.4, 10.6, 18.1, 18.4, 18.5, 18.8, 18.10, 18.11_

  - [x] 12.2 `infra/lambda-role-policy.json` 미러 편집
    - 12.1과 같은 권한 변경을 정책 JSON에 반영한다 (`users/*` Get/Put, `ListBucket` 조건 값, 삭제 문장, 두 SSM 파라미터). 레거시 prefix 권한은 유지
    - 파일 편집만 하고 IAM에 적용하지 않는다
    - _Requirements: 18.4, 18.5, 18.8, 18.10, 18.11_

  - [x] 12.3 `infra/tests/test_iac_config.py` 갱신
    - `Sid`별 단언: `S3Prefixes`는 정확히 Get/Put에 9개 prefix(레거시 8 + `users`), `ListRawPosts`는 `s3:ListBucket`과 `s3:prefix` 값 `raw-posts/*`, `users/*`, `generated-posts/*`, 삭제 문장은 정확히 세 패턴, `*` 액션/리소스 없음
    - 추가 단언: Function URL `AllowHeaders`가 `[Content-Type, X-Mablop-Token]`, 새 파라미터/환경 변수, 두 SSM 경로가 서로 다름, 비밀 값이 템플릿에 없음, 리소스 유형 집합 불변, SSM 문장이 정확히 두 파라미터
    - _Requirements: 17.7, 17.8, 7.1, 7.4, 18.5, 18.8, 18.10, 18.11_

- [x] 13. 프론트엔드 인증 기반
  - [x] 13.1 `frontend/src/format.ts` 추가
    - `formatKst`(`StyleLearningView`에서 옮겨 공용화)와 `formatKstDate`(KST `YYYY-MM-DD`). `StyleLearningView`의 import 전환은 14.4에서 한다
    - _Requirements: 14.3, 14.11, 21.3, 23.2_

  - [x] 13.2 `frontend/src/auth/session.ts` 작성
    - `mablop-session`(`{token, expiresAt, user:{loginId,name,role}}`) 읽기/쓰기/삭제. `readSession(now)`는 JSON 오류, 형태 오류, 만료 시 항목을 지우고 `null`
    - `purgeLegacyStorage()`: 옛 공유 토큰 키와 `mablop-last-draft` 접두 키를 `removeItem`/`startsWith` 루프로 삭제. 두 문자열은 이 파일에만 상수로 두고 제거에만 쓴다
    - _Requirements: 10.2, 10.3, 13.5, 13.6, 23.10, 23.11_

  - [x] 13.3 `frontend/src/api.ts` 변경
    - `postJson<T>(body)`에서 토큰 인자를 없애고 `readSession`의 토큰을 `login`/`register`/`bootstrap-admin`(`PUBLIC_ACTIONS`)을 제외한 모든 요청의 `X-Mablop-Token`에 담는다. `setUnauthorizedHandler(fn)`: 공개 액션이 아닌 요청의 401이면 콜백을 호출한 뒤 `ApiRequestError`를 던진다(`login`의 401은 제외). `fetch` 실패는 `ApiRequestError(0, "network", "서버에 연결할 수 없습니다. 잠시 후 다시 시도하세요.")`, JSON이 아닌 오류 본문에는 한국어 기본 메시지
    - _Requirements: 12.5, 13.7, 13.9, 13.12_

  - [x] 13.4 `frontend/src/auth/AuthContext.tsx` 작성
    - `AuthProvider`와 `useAuth()`(`{user, notice, login, register, logout, replaceToken}`). 시작 시 `purgeLegacyStorage()` 1회, 401 핸들러에서 세션 삭제 + 알림 "로그인이 만료되었습니다. 다시 로그인하세요.". `register`는 로그인하지 않고 백엔드 메시지를 반환, `logout`은 백엔드 호출 없이 세션 삭제, `replaceToken`은 토큰과 `expiresAt`만 교체. 공급자 없이 쓰면 `user: null`과 no-op 함수
    - _Requirements: 10.3, 13.4, 13.5, 13.6, 13.7, 13.8, 14.6, 23.11_

  - [x] 13.5 `frontend/src/views/LoginView.tsx` 작성
    - 중앙 단일 칼럼, `role="tablist"`의 "로그인"/"가입 신청" 탭, 보이는 `<label>`, `autocomplete`(`username`, `current-password`, `new-password`, `name`), `type="password"`. 로그인 필드: 아이디, 비밀번호. 가입 필드: 이름, 아이디, 비밀번호, 비밀번호 확인
    - loginId는 trim + 소문자로 전송, 비밀번호 불일치는 "비밀번호가 일치하지 않습니다."를 보이고 요청하지 않음, 진행 중 제출 버튼 비활성화("처리 중..."), 가입 성공 시 백엔드 메시지를 `role="status"`로 보이고 로그인 모드로 복귀, 오류는 `role="alert"`, 만료 알림 표시, 아이디/비밀번호 규칙 힌트
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.10, 13.11, 13.12, 16.1, 16.2, 16.4, 16.6_

  - [x] 13.6 `frontend/src/grants/useGrants.ts`, `GrantSelect.tsx` 작성
    - `useGrants()`: 관리자와 `user: null`은 `{enabled:false}`로 요청 없음. 멤버는 마운트 시 `grant-list-mine`, `approved`이고 `expired` false인 Grant만 `options`로, 선택은 `mablop-grant-id:{loginId}`에 저장, 목록에 없는 저장값은 삭제하고 선택 없음(자동 선택하지 않음), `canCallBedrock`, `withGrant(body)`(멤버만 `grantId` 추가), `handleGrantError(err)`(403 `forbidden`이면 `reload()` 후 true)
    - `GrantSelect`: 보이는 라벨 "Bedrock 사용 권한", 첫 옵션 "선택하세요", 옵션 문구 "Bedrock 사용 권한 · {승인일} · {만료일}"(KST), 옵션이 없으면 "사용 가능한 Bedrock 사용 권한이 없습니다. 마이페이지에서 신청하세요."를 `role="status"`로 표시, 관리자에게는 아무것도 렌더링하지 않음
    - _Requirements: 21.1, 21.2, 21.3, 21.4, 21.5, 21.6, 21.7, 21.8, 21.9, 21.10_

  - [x]* 13.7 `session.ts` 단위 테스트 작성
    - 파일: `frontend/src/auth/session.test.ts`
    - 읽기/쓰기/만료 삭제, 손상된 JSON, 옛 토큰 키와 `mablop-last-draft*`(`mablop-last-draft:{loginId}` 포함) 삭제 시 `mablop-session`, `mablop-grant-id:{loginId}` 보존
    - _Requirements: 10.3, 13.6, 23.11, 17.9_

  - [x]* 13.8 `api.ts` 테스트 작성 (Property 28)
    - 파일: `frontend/src/api.test.ts` (액션 이름 표로 헤더 유무 확인)
    - **Property 28: The Frontend attaches the token to every action except the three public ones**
    - **Validates: Requirements 13.9**
    - 추가 케이스: 인증 액션의 401은 핸들러 호출, `login`의 401은 미호출, 네트워크 실패와 JSON이 아닌 오류의 한국어 메시지
    - _Requirements: 12.5, 13.7, 13.12_

  - [x]* 13.9 `LoginView` 테스트 작성
    - 파일: `frontend/src/views/LoginView.test.tsx`
    - 두 모드와 라벨, `autocomplete`/`type="password"`, 불일치 메시지와 요청 없음, 진행 중 비활성화, 가입 성공 메시지와 로그인 모드 복귀, 오류 `role`
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.10, 13.11, 13.12, 17.9_

  - [x]* 13.10 `useGrants` 테스트 작성 (Property 38)
    - 파일: `frontend/src/grants/useGrants.test.tsx` (액션 이름 × 역할 표)
    - **Property 38: The Frontend sends `grantId` only on a member's Bedrock_Actions and offers only usable Grants**
    - **Validates: Requirements 21.2, 21.6, 21.9**
    - 추가 케이스: 옵션 필터링, 오래된 저장 id 삭제, `select` 저장, 관리자/무공급자 요청 없음, 403 `forbidden` 시 재로딩
    - _Requirements: 21.4, 21.5, 21.8_

  - [x]* 13.11 `GrantSelect` 테스트 작성
    - 파일: `frontend/src/grants/GrantSelect.test.tsx`
    - 보이는 라벨, KST 날짜 옵션 문구, 빈 상태 문구, 관리자에게 렌더링 없음, 키보드 조작
    - _Requirements: 21.3, 21.7, 21.9, 21.10_

- [x] 14. 프론트엔드 화면
  - [x] 14.1 `frontend/src/views/ProfileView.tsx` 작성 ("내 정보")
    - `get-me`와 `get-learning-status`를 병렬 로딩. 이름/아이디 읽기 전용, 학습 요약(학습한 글, 청크, 최근 학습 KST, 0이면 "아직 학습한 글이 없습니다."), 비밀번호 변경 폼(현재/새/새 확인, 불일치 시 요청 없음, 성공 시 `replaceToken`, 폼 초기화, "비밀번호가 변경되었습니다."), "로그아웃" 버튼, 계정 삭제/사용량 통계 없음
    - "Bedrock 사용 권한" 섹션(멤버만): 선택 "사용 목적" 입력(`maxLength={100}`), `grant-request` 전송 후 폼 초기화와 목록 재조회, 상태 배지(대기, 승인, 거절, 폐기, 만료), 신청 시각 KST, 승인 Grant의 만료일, `requested` Grant가 있으면 폼 비활성화와 "승인 대기 중인 신청이 있습니다.", 빈 목록 "아직 신청한 사용 권한이 없습니다.", 섹션 내부 `role="alert"` 오류 (이 섹션 실패가 페이지 나머지를 가리지 않음)
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8, 14.9, 14.10, 14.11, 14.12, 14.13, 14.14, 16.4, 16.6_

  - [x] 14.2 `frontend/src/views/AdminView.tsx` 작성 ("관리")
    - `admin-list-users`와 `admin-grant-list`를 독립적으로 로딩. "가입 신청"(이름, 아이디, 신청 시각, 승인/거절, 빈 목록 "대기 중인 가입 신청이 없습니다."), "전체 사용자"(이름, 아이디, 상태 대기/활성/비활성, 가입일, 멤버에게만 비활성화/재활성화/비밀번호 초기화)
    - "거절", "비활성화", "비밀번호 초기화"는 행 안에서 "확인"/"취소"로 확인 후 전송, `busy` 집합으로 중복 제출 방지, 행별 `role="alert"` 오류, 성공 후 목록 재로딩. 임시 비밀번호는 컴포넌트 상태에만 두고 복사 버튼, "이 비밀번호는 다시 표시되지 않습니다.", "닫기"를 제공(스토리지에 쓰지 않음)
    - "권한 관리": 신청 대기(이름, 아이디, 사용 목적, 신청 시각, 승인 버튼, 만료 기간 선택 7일/30일/90일 기본 30일, 거절), 발급 내역(이름, 아이디, 배지 승인/거절/폐기/만료, 만료일, 승인되고 만료되지 않은 Grant에만 "폐기"), 거절/폐기 확인, 빈 목록 "대기 중인 사용 권한 신청이 없습니다.", Grant별 오류 표시
    - 레거시 바인딩 실패 알림: `admin-approve-user`의 `legacyBinding.status`가 `failed` 또는 `already_bound`이면 해당 사용자 행 아래에 `role="alert"`로 메시지를 보이고 사용자는 활성으로 유지. `succeeded`/`not_applicable`은 추가 표시 없음. 알림은 컴포넌트 상태에만 둔다
    - _Requirements: 15.1, 15.3, 15.4, 15.5, 15.6, 15.7, 15.8, 15.9, 15.10, 15.11, 15.12, 15.13, 15.14, 15.15, 15.16, 15.17, 15.18, 9.18, 9.19_

  - [x] 14.3 `frontend/src/views/ResultView.tsx` 재작성 (ChatGPT 기록 스타일)
    - 기존 `loading`/`markdown`/`error` props를 `focusDraftId`, `onFocusHandled`로 교체하고 `localStorage`를 읽지 않는다. 열릴 때 `list-drafts`, 좌측 목록(제목, KST 시각, 삭제 버튼 `Trash2` + `aria-label="삭제"`, 최신순, 선택 항목 `aria-current="true"`)과 우측 본문(`get-result` `{kind:"draft", id}`, `.draft-output` `<pre>`, "복사", "다운로드"로 `{draftId}.md` Blob 저장)
    - 빈 목록 "아직 만든 초안이 없습니다.", 삭제는 항목 안에서 "확인"/"취소" 후 `delete-draft`, 성공 시 목록 재로딩과 선택 초기화, 응답이 늦게 도착하면 현재 선택과 비교해 버림, 오류는 목록/본문/항목 위치에 한국어 메시지, `focusDraftId` 자동 선택은 한 번만, Grant 선택기와 `grantId` 없음
    - 변경 테스트: `frontend/src/views/ResultView.test.tsx`를 기록 화면 기준으로 교체 (`list-drafts` 호출, 좌우 레이아웃, 항목 구성과 최신순, 빈 문구, 선택/복사/다운로드, `focusDraftId` 자동 선택, 삭제 확인/재로딩/본문 초기화, 영역별 오류, 640px 이하 한 칼럼 스타일 확인, `localStorage` 접근 없음). 이전 테스트가 지키던 동작의 단언 강도는 유지
    - _Requirements: 17.7, 17.8, 20.8, 20.9, 23.1, 23.2, 23.3, 23.4, 23.5, 23.6, 23.7, 23.8, 23.9, 23.10, 23.12_

  - [x] 14.4 `StyleLearningView.tsx` 수정
    - `TokenField`, 토큰 상태, `localStorage` 토큰 코드 제거, 호출은 `postJson(body)`, `loadLearningStatus`의 `if (!token) return` 제거, 미리보기 안내를 "네이버 블로그 URL을 먼저 입력하세요."로 변경, `formatKst`는 `format.ts`에서 import
    - `GrantSelect`를 폼 위에 렌더링하고 `learn-style`, `rebuild-from-stored`를 `withGrant`로 전송, `issue-presigned-urls`/`get-learning-status`/`preview-learning-sources`는 `grantId` 없음, Grant 선택 전/옵션 없음이면 Bedrock 버튼 비활성화, 403 `forbidden`은 기존 오류 영역에 메시지를 보이고 `handleGrantError`로 옵션 재로딩
    - 변경 테스트: `frontend/src/views/StyleLearningView.test.tsx` (토큰 필드 제거, 멤버 세션 + `grant-list-mine` 모의, `grantId` 전송/버튼 비활성화/403 케이스 추가, 기존 단언 유지)
    - _Requirements: 10.2, 17.7, 17.8, 21.1, 21.5, 21.6, 21.7, 21.8, 21.9_

  - [x] 14.5 `DraftingView.tsx` 수정
    - `TokenField`/토큰 코드 제거, `postJson(body)`, `GrantSelect` 렌더링, `analyze-images`와 `generate-draft`는 `withGrant`, `issue-presigned-urls`는 `grantId` 없음. 초안을 `localStorage`에 쓰지 않고 메모리 미리보기/복사/다운로드를 유지, 각 `generate-draft` 응답의 `draftId`로 `onDraftCreated(draftId)` 호출. 제목/개요/메모 안내 문구는 이미 완료되어 건드리지 않는다
    - 변경 테스트: `frontend/src/views/DraftingView.test.tsx`, `frontend/src/views/DraftingView.contract.test.tsx` (토큰 제거, `grantId` 전송/비전송 단언 추가, `onDraftCreated` 호출 확인, `mablop-last-draft` 쓰기 단언을 "`localStorage`에 아무것도 쓰지 않음"으로 교체)
    - _Requirements: 10.2, 17.7, 17.8, 21.1, 21.6, 21.7, 21.8, 21.9, 23.5, 23.10_

  - [x] 14.6 `App.tsx` 변경과 `TokenField.tsx` 삭제
    - `AuthProvider`로 감싸고 세션이 없으면 `LoginView`, 있으면 앱 셸. 탭: 스타일 학습, 초안 작성, 결과, 내 정보, 관리자에게만 관리(멤버에게는 `AdminView`를 렌더링하지 않음). 탭 줄은 좁은 화면에서 가로 스크롤
    - `latestDraftId` 상태를 두고 `DraftingView`의 `onDraftCreated`를 받아 `ResultView`에 `focusDraftId`/`onFocusHandled`로 넘김
    - `frontend/src/TokenField.tsx` 삭제, `mablop-token`/토큰 입력 참조 제거
    - 변경 테스트: `frontend/src/App.test.tsx`가 유효한 세션을 `localStorage`에 심고 스타일 학습 화면을 확인하도록 수정
    - _Requirements: 10.2, 13.1, 13.5, 13.8, 14.1, 15.1, 15.2, 17.7, 23.5_

  - [x] 14.7 `frontend/src/index.css` 확장
    - 기존 변수만 사용: `.auth-shell`(중앙 칼럼 360px, `min-height: 100dvh`), `.mode-tabs`, `.badge`(`-active`, `-pending`, `-disabled`), `.user-row`, `.confirm-inline`, `.secret-box`, `.info-list`, `.grant-row`, `.history-layout`(2열, 기존 `max-width: 640px` 블록에서 1열), `.history-list`, `.history-item`(`.active` 변형은 accent-soft 배경, 그라데이션 없음), `.history-body`
    - 좁은 화면에서 탭 줄 `overflow-x: auto`와 `flex: 0 0 auto`, 거친 포인터에서 버튼 최소 높이 44px 유지, `.token-saved` 규칙 제거. 그라데이션, 이모지, 새 UI 라이브러리 없음
    - _Requirements: 16.1, 16.2, 16.3, 16.6, 23.9, 23.12_

  - [x]* 14.8 `ProfileView` 테스트 작성
    - 파일: `frontend/src/views/ProfileView.test.tsx`
    - 읽기 전용 이름/아이디, 학습 요약과 KST 시각, 0건 문구, 비밀번호 변경 성공/불일치, 로그아웃, 초안 목록/삭제 컨트롤 없음, "Bedrock 사용 권한" 섹션(멤버 표시/관리자 없음, 100자 제한, 신청 후 폼 초기화와 재조회, 다섯 가지 배지, 대기 중 비활성화 문구, 빈 문구, 오류 문구)
    - _Requirements: 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8, 14.9, 14.10, 14.11, 14.12, 14.13, 14.14, 17.9_

  - [x]* 14.9 `AdminView` 테스트 작성
    - 파일: `frontend/src/views/AdminView.test.tsx`
    - 가입 신청/전체 사용자 목록, 역할·상태별 컨트롤 표시, 확인/취소 흐름, 임시 비밀번호 표시/복사/닫기, 재로딩, 빈 문구, 행별 오류. "권한 관리": 신청 대기 목록, 만료 선택(기본 30일, 7/30/90 전송), 거절/폐기 확인, 폐기 버튼 조건, 발급 내역 배지, 액션 후 재로딩, 빈 문구, Grant별 오류. 레거시 바인딩 알림(`failed`/`already_bound` 표시 + 사용자 활성 유지, `succeeded`/`not_applicable` 없음)
    - _Requirements: 15.3, 15.4, 15.5, 15.6, 15.7, 15.8, 15.9, 15.10, 15.11, 15.13, 15.14, 15.15, 15.16, 15.17, 15.18, 17.9_

  - [x]* 14.10 앱 인증 흐름 테스트 작성
    - 파일: `frontend/src/App.auth.test.tsx`
    - 로그인 게이트, 만료된 저장 세션 삭제, "관리" 탭은 관리자에게만, 로그아웃, 401 후 "로그인이 만료되었습니다. 다시 로그인하세요."
    - _Requirements: 13.1, 13.6, 13.7, 13.8, 15.1, 15.2, 17.9_

  - [x]* 14.11 레거시 키 문자열 정적 검사 테스트 작성
    - 파일: `frontend/src/legacy-keys.test.ts`
    - `session.ts`(와 이 테스트 파일) 외의 소스에 옛 공유 토큰 키 문자열과 `mablop-last-draft`가 없고, `TokenField` 참조와 비밀 토큰 입력 필드가 없음을 확인 (14.6 이후에 통과)
    - _Requirements: 10.2, 23.10, 17.9_

- [x] 15. Checkpoint - 프론트엔드 범위 최소 검증
  - 13, 14에서 만든/바꾼 테스트 파일만 실행한다 (예: `npx vitest run src/auth src/grants src/views src/api.test.ts src/App.test.tsx`). 전체 vitest와 빌드는 마지막 체크포인트(17)에서 한 번에 실행한다.
  - Ensure all tests pass, ask the user if questions arise.

- [x] 16. 문서와 runbook
  - [x] 16.1 문서 갱신
    - `infra/README.md`: 새 파라미터, 환경 변수, S3/SSM/삭제 권한 변경과 "적용 전 사용자 확인" 안내
    - `docs/aidlc/inception/security.md`: 인증 모델(HMAC 토큰, PBKDF2, 스로틀, 공개 액션 집합), 사용자별 데이터 분리, Grant 모델, 알려진 한계(로그아웃 토큰 미폐기, localStorage 토큰, 사용량 한도 없음)
    - `docs/aidlc/operations/deploy-runbook.md`: 설계의 릴리스 순서 1~8(사용자 확인 단계 표시), 서명 비밀 생성 방법, `bootstrap-admin` 호출 방법, `jhe8888` 승인 후 확인 항목, `migrate-legacy-data` 재시도 방법, 로컬 `sam build` ASCII 스테이징 경로 `C:\mablop-deploy`
    - 이 작업은 문서만 수정하며 어떤 환경에도 적용하지 않는다
    - _Requirements: 9.20, 10.4, 10.5, 10.6, 18.4, 18.5, 18.6, 18.7, 18.8_

- [x] 17. 최종 체크포인트 - 전체 테스트 한 번에 실행
  - 개발 단계가 모두 끝난 뒤 이 한 번만 전체 스위트를 실행한다. 구현 중간에는 작업 파일 범위의 최소 검증만 했다.
  - 실행 목록: ① `backend/`에서 `pytest` 전체 ② `frontend/`에서 vitest 단일 실행(`npm test`)과 `npm run build` ③ 루트에서 infra 테스트(`pytest infra/tests`) ④ `sam validate --lint --template-file infra/template.yaml`
  - 환경 제약 메모(아래 "환경 제약")를 따른다. 실패가 Req 17.7의 변경 허용 사유가 아니면 테스트가 아니라 구현이나 설계를 의심한다
  - 기존 테스트 기준: 백엔드 163개, 프론트엔드 26개가 모두 통과해야 한다 (변경 테스트는 위 작업에 명시된 파일로 한정)
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- `*`가 붙은 하위 작업(속성 테스트, 단위 테스트)은 선택 사항이며 빠른 MVP에서는 건너뛸 수 있다. 단, 토큰 서명/만료(속성 1, 2), 사용자 격리(속성 15), Grant 검증(속성 29), 마이그레이션 멱등성(속성 17, 18, 33), 초안 인덱스 정합성(속성 35~37)은 핵심 속성이므로 가능하면 건너뛰지 않는다.
- 선택 표시가 없는 테스트 작업은 기존 테스트를 바꾸는 것이 구현의 일부이거나(예: 7.1, 7.2, 9.x, 14.3~14.6), 사용자가 명시적으로 요청한 작업(9.7 `preview-learning-sources` 백엔드 테스트)이다.
- 이 계획에서 이름이 지정된 변경 기존 테스트: `test_errors.py`(1.1), `conftest.py`(2.2), `test_access_controller.py`(7.1), `test_handler.py`(7.2), `test_index_store.py`, `test_style_profile.py`, `test_checkpoint*.py`(필요 시)(9.1), `test_style_learner_flow.py`(9.2, 9.3), `test_image_analyzer_presigned.py`, `test_image_analyzer_caption.py`(9.4), `test_drafting_engine_flow.py`, `test_drafting_engine_generate.py`(9.5), `infra/tests/test_iac_config.py`(12.3), `ResultView.test.tsx`(14.3), `StyleLearningView.test.tsx`(14.4), `DraftingView.test.tsx`, `DraftingView.contract.test.tsx`(14.5), `App.test.tsx`(14.6). 설계에 나온 테스트 파일 이름 일부는 병렬 실행 중 파일 충돌을 피하려고 속성별 파일로 나누었다.
- 속성 테스트는 hypothesis로 최소 100회, `deadline=None`, 태그 `Feature: user-auth-admin, Property N: <title>`을 붙인다. PBKDF2는 반복 횟수를 줄이거나 `PBKDF2_ITERATIONS`를 monkeypatch한다. 시간은 `now` 인자로 주입하고, S3는 moto `mock_aws`를 쓰며 실제 AWS를 호출하지 않는다.
- 프론트엔드는 속성 테스트 라이브러리를 추가할 수 없어(Req 17.10) 속성 28, 38을 액션 이름 × 역할 표를 도는 예시 테스트로 구현한다.
- 모든 요구사항 문구는 한국어 메시지 리터럴을 그대로 비교하는 테스트로 고정한다 (메시지 모듈 상수와 비교하지 않는다).

## 환경 제약

- 셸은 PowerShell 5이며 `&&`를 지원하지 않는다. 명령은 `;`로 잇거나 별도 호출로 나누고 `cwd`를 지정한다.
- 한글이 들어간 파일은 UTF-8(BOM 없음)로 안전하게 기록한다. PowerShell에서는 here-string과 .NET `WriteAllText(path, text, New-Object System.Text.UTF8Encoding($false))`를 사용하고, `Out-File`/`>`/`Set-Content` 기본 인코딩은 쓰지 않는다. 기록 후 한글이 깨지지 않았는지(`?` 연속, U+FFFD) 다시 읽어 확인한다.
- 로컬 `sam build`는 한글 경로에서 실패할 수 있으므로 ASCII 스테이징 경로 `C:\mablop-deploy`에서 실행한다.
- 명령 출력이 삼켜지면 결과를 파일로 저장한 뒤 읽는다 (예: `pytest ... > out.txt 2>&1` 후 파일 읽기).
- pytest 또는 vitest 프로세스가 중복으로 남아 venv 파일 락을 잡으면 해당 프로세스를 종료한 뒤 다시 실행한다.
- 개발 서버, 감시 모드(`vitest` watch, `webpack --watch` 등)는 실행하지 않는다. 테스트는 단일 실행 옵션을 쓴다.

## 사용자 확인 필요 항목 (자동 실행 제외)

아래 항목은 체크박스 작업이 아니다. 어떤 에이전트도 자동으로 실행하지 않으며, 각 항목마다 소유자의 명시적 확인을 받은 뒤 소유자가 진행하거나 지시한다 (Req 18.4~18.9).

- IAM 반영: `infra/template.yaml`, `infra/lambda-role-policy.json`의 권한 변경(`users/*` Get/Put, `ListBucket` 조건 확장, `s3:DeleteObject` 세 패턴, 서명 비밀 SSM) 검토와 적용. `s3:DeleteObject`가 모든 사용자의 초안/계정 객체에 미치는 범위(설계 Deviations 1)를 이때 함께 검토한다.
- SSM 서명 비밀 생성: `/mablop/signing-secret` SecureString(32자 이상 무작위) 생성.
- 배포: 백엔드(SAM)와 프론트엔드(GitHub Pages) 배포. `main`으로의 push/merge는 `deploy-backend`, `deploy-frontend` 워크플로를 실행하므로 push 전에 확인을 받는다.
- 프로덕션 `bootstrap-admin` 실행: 배포 확인 직후 부트스트랩 토큰으로 1회 실행. 결과로 관리자 계정이 만들어지고 학습 상태가 0/0인지 확인한다.
- `jhe8888` 가입 신청 승인과 레거시 바인딩 실제 실행: 승인 시 자동으로 실행되며, 실패하면 `migrate-legacy-data`를 API로 재시도한다. 승인 후 9개 글, 31 청크, 이전 초안 목록을 확인한다.
- 운영 Grant 승인: 각 멤버의 첫 Bedrock 사용 신청을 관리자 화면에서 만료 기간과 함께 승인한다.
- 개발 환경 데이터 변경: 개발 버킷이 아닌 프로덕션 버킷에 대한 데이터 변경 명령은 확인 없이 실행하지 않는다.
- 사후 정리(훨씬 나중, 소유자 결정): 마이그레이션 검증 후 원본 레거시 객체 삭제, 정책에서 레거시 prefix 권한 제거, `/mablop/token` 삭제 여부 결정.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "12.1", "12.2", "13.1", "13.2", "14.7"] },
    { "id": 1, "tasks": ["1.3", "12.3", "13.3", "13.7"] },
    { "id": 2, "tasks": ["1.4", "1.5", "2.1", "13.4", "13.8"] },
    { "id": 3, "tasks": ["2.2", "2.8", "2.10", "13.5", "13.6"] },
    { "id": 4, "tasks": ["2.3", "2.4", "2.5", "2.6", "2.7", "2.9", "2.11", "2.12", "2.13", "4.1", "13.9", "13.10", "13.11", "14.1", "14.2", "14.3", "14.4", "14.5"] },
    { "id": 5, "tasks": ["4.2", "5.1", "6.1", "14.6", "14.8", "14.9"] },
    { "id": 6, "tasks": ["4.3", "4.4", "4.5", "4.6", "4.7", "4.8", "4.9", "4.10", "4.11", "4.12", "4.13", "5.2", "5.3", "5.4", "5.5", "6.2", "6.3", "6.4", "6.5", "6.6", "7.1", "14.10"] },
    { "id": 7, "tasks": ["7.2", "14.11"] },
    { "id": 8, "tasks": ["7.4", "7.5", "7.6", "7.7", "7.8", "7.9", "7.10", "7.11", "7.12"] },
    { "id": 9, "tasks": ["9.1", "9.4"] },
    { "id": 10, "tasks": ["9.2", "9.5"] },
    { "id": 11, "tasks": ["9.3", "9.6", "9.7"] },
    { "id": 12, "tasks": ["9.8", "9.9", "9.10", "9.11", "10.1"] },
    { "id": 13, "tasks": ["7.3", "10.2"] },
    { "id": 14, "tasks": ["10.3", "10.4", "10.5", "10.6", "16.1"] }
  ]
}
```
