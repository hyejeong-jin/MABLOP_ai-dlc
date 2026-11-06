# Requirements Document

## Introduction

MABLOP currently gates every Backend request with one Pre_Shared_Token that all users share. Style-learning data (raw posts, Vector_Index, Style_Profile, learning status) lives in one shared S3 space. This feature replaces the shared token with per-user accounts, so that each of the 2-3 trusted users has a private workspace and one administrator approves new users.

Scope:

- Account registration with administrator approval (status `pending` → `active`, or `disabled`).
- Login with a stateless, HMAC-signed Session_Token. Passwords are hashed with PBKDF2 from the Python standard library. The accounts are stored as JSON in the existing S3 bucket. The feature adds no AWS service, no Python package, and no npm package.
- Per-user data separation under the S3 prefix `users/{userId}/`.
- A one-time Bootstrap that creates the first administrator with the existing SSM token. The administrator starts with an empty workspace. Afterwards the shared-token path and the Frontend `TokenField` are removed.
- Legacy data binding: the existing learning data (9 posts, 31 chunks), the raw posts, and the earlier drafts are copied to the member whose loginId is `jhe8888`, once, at the moment the administrator approves that member.
- Bedrock usage Grants: a member requests permission, the administrator approves with an expiry, and the Backend checks a non-secret `grantId` before every action that can call Bedrock.
- Draft history: every generated draft is recorded in a per-user index, and the Results tab shows a deletable, ChatGPT-style history list.
- Three Frontend areas: login/registration screen, user page, administrator page. The learning, drafting, and Results views gain a Grant selector and the draft history.
- Security controls: an explicit list of actions exempt from authentication, brute-force throttling, password and secret handling, token expiry, and error messages that do not reveal which accounts exist.

Out of scope: call quotas or usage limits per Grant, account deletion, per-user usage statistics, e-mail, MFA, social login, self-service password recovery, administrator password recovery, role changes, server-side token revocation at logout (the Session_Token stays valid until expiry; disabling a user and changing or resetting a password do invalidate tokens), and deletion of the original shared data. The instruction text under the title, outline, and notes inputs of the drafting screen is already done and is not part of this document.

## Glossary

- **MABLOP_System**: The complete MABLOP application: Frontend, Backend, S3 storage, and Bedrock integrations.
- **Backend**: The single Python Lambda behind a public Function URL, routed by `action` through `handler.DISPATCH`.
- **Frontend**: The React (TypeScript) single-page application hosted on GitHub Pages.
- **Auth_Service**: The new Backend component that implements registration, login, password change, profile, administrator actions, Bootstrap, and authentication throttling.
- **Access_Controller**: The existing Backend component (`access_controller.py`) that checks body length, global request rate, and credentials before dispatch.
- **S3_Store**: The existing `s3store.py` helpers over the single private bucket, extended with a user-scoped key builder.
- **Legacy_Migrator**: The Backend routine that copies the pre-existing shared learning data and drafts (Legacy_Keys and Legacy_Drafts) into the User_Prefix of the Legacy_Owner.
- **LEGACY_OWNER_LOGIN_ID**: The Backend constant with the value `jhe8888`. It names the loginId whose approved account receives the legacy data.
- **Legacy_Owner**: The User whose loginId equals LEGACY_OWNER_LOGIN_ID. The Legacy_Owner has role `member` and registers through the ordinary `register` path.
- **Legacy_Binding_Record**: The create-once JSON object `users/_auth/legacy-owner.json` that holds the userId to which the legacy data is bound.
- **Legacy_Drafts**: The pre-existing shared S3 objects `generated-posts/*.md`.
- **Grant**: A non-secret record of the permission of one User to use Bedrock, with `id`, `status`, optional `purpose`, `requestedAt`, `decidedAt`, and `expiresAt`. A Grant contains no secret string.
- **grantId**: The identifier of a Grant, formed as `g_` followed by 16 lowercase hexadecimal characters. A grantId is not a secret.
- **Grant_Status**: One of `requested`, `approved`, `rejected`, or `revoked`, stored in the Grant. The derived display state "만료" applies to an `approved` Grant whose `expiresAt` is earlier than the current time.
- **Grant_Store**: The S3 object `users/{userId}/metadata/grants.json` of the form `{"v":1,"grants":[...]}` that holds the Grants of one User.
- **Bedrock_Actions**: The actions `learn-style`, `analyze-images`, `generate-draft`, `rebuild-from-stored`, and `get-result` with a `kind` other than `draft`. The last routes to `style_learner.learn_style` and can call Bedrock.
- **GrantSelect**: The Frontend dropdown "Bedrock 사용 권한" that lists the approved, unexpired Grants of the logged-in User.
- **Draft_Index**: The S3 object `users/{userId}/metadata/drafts.json` that holds entries of the form `{draftId, title, createdAt}` for the Drafts of one User.
- **Draft_History_List**: The Frontend list on the Results tab that shows the Draft_Index entries of the logged-in User, newest first.
- **User**: A person with a User_Record. A User has role `member` or `admin`.
- **Admin_User**: A User whose role is `admin`. The system holds exactly one Admin_User, created by Bootstrap.
- **User_Record**: A JSON object stored at `users/{userId}/account.json` with `userId`, `loginId`, `name`, `role`, `status`, password hash parameters, `createdAt`, `approvedAt`, and `credentialsChangedAt`.
- **userId**: An opaque identifier of the form `u_` followed by 16 lowercase hexadecimal characters, generated randomly at registration.
- **loginId**: The user-chosen login name, 4 to 32 characters from lowercase `a-z`, digits `0-9`, underscore, and hyphen.
- **Account_Status**: One of `pending` (registered, not approved), `active` (may log in), or `disabled` (blocked by an Admin_User).
- **Session_Token**: A string carrying `userId`, `issuedAt`, and `expiresAt`, signed with HMAC-SHA256 using the Signing_Secret.
- **Signing_Secret**: A secret of at least 32 characters stored as an SSM Parameter Store SecureString and used only to sign and verify Session_Tokens.
- **Bootstrap_Token**: The existing Pre_Shared_Token stored in SSM, reused only for the one-time `bootstrap-admin` action.
- **Pre_Shared_Token**: The single shared secret that the current Access_Controller requires on every request.
- **Auth_Exempt_Actions**: The fixed set of actions that Access_Controller dispatches without a valid Session_Token: `login`, `register`, and `bootstrap-admin`.
- **User_Prefix**: The S3 key prefix `users/{userId}/` that holds all data of one User.
- **Auth_Shared_Prefix**: The S3 key prefix `users/_auth/` that holds data shared by authentication logic (the loginId-to-userId lookup and the throttle state). The leading underscore cannot collide with a userId.
- **Login_Throttle**: The persisted counters that limit failed login attempts and registration requests.
- **Legacy_Keys**: The pre-existing shared S3 objects: `raw-posts/*.txt`, `embeddings/index.npy`, `embeddings/index-map.json`, `vector-index/manifest.json`, `style-profile/profile.json`, and `metadata/learning-status.json`.
- **Style_Learner**: The existing Backend component for style learning (`style_learner.py`).
- **Drafting_Engine**: The existing Backend component for draft generation (`drafting_engine.py`).
- **Image_Analyzer**: The existing Backend component for presigned image uploads and captioning (`image_analyzer.py`).
- **Existing_Actions**: The eight actions in `handler.DISPATCH` before this feature: `issue-presigned-urls`, `learn-style`, `analyze-images`, `generate-draft`, `get-result`, `get-learning-status`, `preview-learning-sources`, and `rebuild-from-stored`.
- **Calm_Design_Guide**: The project UI guide: Pretendard font, warm neutral palette with one green accent, no gradients, no emoji, `lucide-react` icons.

## Requirements

### Requirement 1: Registration Request

**User Story:** As a prospective user, I want to submit a registration request, so that an administrator can approve my access.

#### Acceptance Criteria

1. WHEN the Auth_Service receives a `register` request with a valid `loginId`, `name`, and `password`, THE Auth_Service SHALL create a User_Record with role `member` and Account_Status `pending`.
2. WHEN the Auth_Service creates a pending User_Record, THE Auth_Service SHALL respond with the message "가입 신청이 접수되었습니다. 관리자 승인 후 로그인할 수 있습니다."
3. WHEN the Auth_Service receives a `register` request whose `loginId` already belongs to a User_Record in any Account_Status, THE Auth_Service SHALL create no record and SHALL return the same response as in criterion 2.
4. IF a `register` request has a `loginId` outside the loginId format, a `name` shorter than 1 or longer than 30 characters, or a `password` that violates the password policy in Requirement 3, THEN THE Auth_Service SHALL return a `bad_request` error with a Korean message that names the invalid field and its format rule.
5. THE Auth_Service SHALL assign each new User a randomly generated userId and SHALL record `createdAt` in UTC ISO-8601 format.
6. THE Auth_Service SHALL store the loginId-to-userId lookup under the Auth_Shared_Prefix in a form that returns the userId for a given loginId with one S3 read.

### Requirement 2: Login and Session Token

**User Story:** As an approved user, I want to log in with my ID and password, so that I can use MABLOP with my own data.

#### Acceptance Criteria

1. WHEN the Auth_Service receives a `login` request with a loginId and password that match an `active` User_Record, THE Auth_Service SHALL return a Session_Token, its `expiresAt`, and the User's `name`, `loginId`, and `role`.
2. THE Auth_Service SHALL set the Session_Token lifetime to 8 hours from issuance and SHALL provide no refresh mechanism.
3. THE Auth_Service SHALL build each Session_Token as a base64url-encoded payload followed by a base64url-encoded HMAC-SHA256 signature computed with the Signing_Secret.
4. IF a `login` request names a loginId that has no User_Record, or supplies a password that does not match, THEN THE Auth_Service SHALL return HTTP 401 with the identical message "아이디 또는 비밀번호가 올바르지 않습니다."
5. WHEN a `login` request names a loginId that has no User_Record, THE Auth_Service SHALL perform one PBKDF2 computation with the same iteration count as real records before responding, so that response time does not distinguish unknown loginIds from wrong passwords.
6. IF a `login` request has a matching password and the User_Record has Account_Status `pending`, THEN THE Auth_Service SHALL return HTTP 403 with the message "승인 대기 중인 계정입니다."
7. IF a `login` request has a matching password and the User_Record has Account_Status `disabled`, THEN THE Auth_Service SHALL return HTTP 403 with the message "사용이 중지된 계정입니다."

### Requirement 3: Credential Storage and Password Policy

**User Story:** As the system owner, I want passwords stored only as salted hashes made with the standard library, so that a leaked bucket object does not expose passwords and the feature adds no dependency.

#### Acceptance Criteria

1. THE Auth_Service SHALL hash each password with `hashlib.pbkdf2_hmac` using SHA-256, 600000 iterations, and a 16-byte salt produced by the `secrets` module.
2. THE Auth_Service SHALL store the algorithm name, iteration count, salt, and hash in the User_Record, and SHALL read the stored iteration count when verifying a password.
3. THE Auth_Service SHALL compare password hashes and signatures with `hmac.compare_digest`.
4. THE Auth_Service SHALL accept passwords of 10 to 128 characters and SHALL apply no other composition rule.
5. THE Auth_Service SHALL keep plaintext passwords out of S3 objects, log output, error messages, and responses, with the single exception of the temporary password defined in Requirement 11.
6. THE Auth_Service SHALL use only Python standard-library modules for hashing, token signing, and random value generation.

### Requirement 4: Request Authentication and Exempt Actions

**User Story:** As the system owner, I want every request authenticated except an explicit short list, so that unauthenticated callers can reach only login, registration, and one-time Bootstrap.

#### Acceptance Criteria

1. THE Access_Controller SHALL define Auth_Exempt_Actions as exactly `login`, `register`, and `bootstrap-admin`.
2. WHEN a request arrives, THE Access_Controller SHALL run the body-length check, then the global rate check, then read `action`, then authenticate the request, in that order, before any component runs.
3. WHEN the `action` is not in Auth_Exempt_Actions, THE Access_Controller SHALL require a Session_Token with a valid signature, an `expiresAt` in the future, a User_Record whose Account_Status is `active`, and an `issuedAt` not earlier than the User_Record's `credentialsChangedAt`.
4. IF Session_Token verification fails for any reason, THEN THE Access_Controller SHALL return HTTP 401 with the message "로그인이 필요합니다. 다시 로그인하세요." and SHALL not state which check failed.
5. IF a request names an unknown `action` and carries no valid Session_Token, THEN THE Access_Controller SHALL return HTTP 401 instead of `bad_request`.
6. WHEN the `action` is `bootstrap-admin`, THE Access_Controller SHALL verify the Bootstrap_Token with the existing constant-time `check_token` comparison.
7. THE Access_Controller SHALL derive the acting userId only from the verified Session_Token and SHALL ignore any userId, loginId, or S3 key prefix supplied in the request body for authorization.
8. WHEN an action is administrator-only, THE Access_Controller SHALL return HTTP 403 with the message "관리자 권한이 필요합니다." unless the role in the current User_Record is `admin`.
9. THE Access_Controller SHALL read the Session_Token from the request header that the Function URL CORS configuration already permits, so that no CORS change is needed.

### Requirement 5: Brute-Force and Abuse Throttling

**User Story:** As the system owner, I want repeated failed logins and mass registration blocked, so that passwords cannot be guessed and the bucket cannot be filled by anonymous callers.

#### Acceptance Criteria

1. THE Access_Controller SHALL continue to apply the existing global limit of 60 requests per 60 seconds to all actions, including Auth_Exempt_Actions.
2. WHEN 5 failed login attempts for one loginId occur within 10 minutes, THE Auth_Service SHALL reject further login attempts for that loginId for 10 minutes with HTTP 429 and the message "로그인 시도가 너무 많습니다. 10분 후 다시 시도하세요."
3. WHILE a loginId is blocked, THE Auth_Service SHALL reject login attempts for that loginId even when the password is correct.
4. THE Auth_Service SHALL count failed attempts and apply the block for loginIds that have no User_Record in the same way as for existing loginIds.
5. WHEN a login succeeds, THE Auth_Service SHALL reset the failure counter of that loginId.
6. WHEN 30 failed login, change-password, or `bootstrap-admin` attempts occur within 10 minutes across all callers, THE Auth_Service SHALL reject further login and `bootstrap-admin` attempts for 10 minutes with HTTP 429.
7. WHEN more than 5 `register` requests occur within 60 minutes across all callers, THE Auth_Service SHALL reject further `register` requests until the window ends, with HTTP 429 and the message "가입 신청이 너무 많습니다. 잠시 후 다시 시도하세요."
8. WHILE 10 User_Records have Account_Status `pending`, THE Auth_Service SHALL reject new `register` requests with HTTP 429 and the message "현재 가입 신청을 받을 수 없습니다. 관리자에게 문의하세요."
9. THE Auth_Service SHALL persist Login_Throttle state as one JSON object under the Auth_Shared_Prefix, SHALL remove expired entries on every write, and SHALL hold at most 200 entries by evicting the oldest.
10. WHEN a `change-password` request supplies a wrong current password, THE Auth_Service SHALL count the failure toward the failure counter of that User's loginId.

### Requirement 6: Disclosure and Logging Safeguards

**User Story:** As the system owner, I want responses and logs that reveal nothing useful to an attacker, so that account enumeration and credential leakage are not possible through the API.

#### Acceptance Criteria

1. THE Auth_Service SHALL return identical status, message, and body shape for the cases "loginId unknown" and "password wrong" on `login`.
2. THE Auth_Service SHALL return the identical success response for `register` whether or not the loginId exists.
3. THE Auth_Service SHALL exclude password hash parameters, salt, Signing_Secret, and Bootstrap_Token from every response.
4. THE Auth_Service SHALL limit each authentication log entry to the event type, the outcome, and the userId of an existing User.
5. THE Auth_Service SHALL omit submitted loginId values of unknown Users, passwords, hashes, salts, Session_Tokens, Signing_Secret, Bootstrap_Token, and temporary passwords from log output.
6. IF an unexpected exception occurs while handling an authentication or administrator action, THEN THE Auth_Service SHALL return `internal_error` with the message "일시적인 오류가 발생했습니다. 잠시 후 다시 시도하세요." and SHALL omit the exception text and stack trace from the response.

### Requirement 7: Signing Secret Management

**User Story:** As the system owner, I want the token-signing secret kept in SSM, so that the secret never appears in source, templates, environment variables, or S3.

#### Acceptance Criteria

1. THE Auth_Service SHALL read the Signing_Secret from an SSM SecureString whose path comes from an environment variable and differs from the Bootstrap_Token path.
2. THE Auth_Service SHALL cache the Signing_Secret in memory for the lifetime of a warm Lambda container.
3. IF the Signing_Secret is missing, unreadable, or shorter than 32 characters, THEN THE Auth_Service SHALL fail every login and every Session_Token verification with `internal_error` and the message in Requirement 6.6.
4. THE MABLOP_System SHALL keep the Signing_Secret value out of source code, CloudFormation templates, Lambda environment variables, S3 objects, logs, and responses.

### Requirement 8: Per-User Data Separation

**User Story:** As a user, I want my learning data, drafts, and images stored apart from other users' data, so that other users' content never mixes into my drafts.

#### Acceptance Criteria

1. THE S3_Store SHALL provide a key builder that takes a userId and a relative path and returns `users/{userId}/{relative path}`.
2. IF the userId does not match `^u_[0-9a-f]{16}$`, or the relative path is empty, starts with `/`, or contains `..`, THEN THE key builder SHALL raise an error and SHALL not return a key.
3. THE Style_Learner SHALL store and read raw posts at `users/{userId}/raw-posts/`, the index at `users/{userId}/embeddings/` and `users/{userId}/vector-index/`, the Style_Profile at `users/{userId}/style-profile/profile.json`, and the learning status and learn checkpoints at `users/{userId}/metadata/`.
4. THE Drafting_Engine SHALL retrieve Top_K Post_Chunks only from the index in the acting User's User_Prefix and SHALL read only the acting User's Style_Profile.
5. THE Drafting_Engine SHALL store each Draft at `users/{userId}/generated-posts/{draftId}.md`, and `get-result` for kind `draft` SHALL read only from the acting User's User_Prefix.
6. THE Image_Analyzer SHALL issue presigned upload keys under `users/{userId}/images/`.
7. IF an image key in an `analyze-images` or `generate-draft` request does not start with the acting User's `users/{userId}/images/`, THEN THE Backend SHALL return a `bad_request` error with the message "허용되지 않는 이미지 경로입니다." and SHALL read no object.
8. WHEN a User has no learning data, THE Style_Learner SHALL return `get-learning-status` with `processedPosts` 0, `chunks` 0, and an empty `posts` list.
9. THE Backend SHALL keep the global request-rate state at `metadata/ratelimit.json`, outside every User_Prefix.
10. WHEN this feature is complete, THE Backend SHALL read Legacy_Keys and Legacy_Drafts only inside the Legacy_Migrator.

### Requirement 9: Administrator Bootstrap and Legacy Data Binding

**User Story:** As the system owner, I want to create the first administrator once with the existing token and to bind my current learning data and drafts to the member jhe8888 when I approve that member, so that I lose no data and need no extra service.

#### Acceptance Criteria

1. WHEN the Backend receives a `bootstrap-admin` request with `loginId`, `name`, and `password`, THE Auth_Service SHALL verify the Bootstrap_Token before reading any other input.
2. WHILE no User_Record with role `admin` exists, THE Auth_Service SHALL create one with role `admin` and Account_Status `active` on a valid `bootstrap-admin` request, without running the Legacy_Migrator.
3. IF a `bootstrap-admin` request carries a valid Bootstrap_Token and an Admin_User already exists, THEN THE Auth_Service SHALL create no account and SHALL return HTTP 403 with the message "이미 관리자 계정이 있습니다."
4. WHEN the Auth_Service creates the Admin_User, THE Auth_Service SHALL leave the Admin_User's User_Prefix without learning data, so that the Admin_User starts with an empty workspace and `get-learning-status` reports `processedPosts` 0 and `chunks` 0.
5. THE Legacy_Migrator SHALL copy each Legacy_Key and each Legacy_Draft to the same relative path under the target User's User_Prefix, and SHALL leave learn checkpoints, `images/*`, and `metadata/ratelimit.json` uncopied.
6. THE Legacy_Migrator SHALL keep every original Legacy_Key and Legacy_Draft object unchanged and SHALL issue no delete or overwrite request against them, including when a copy fails.
7. WHEN a destination object already exists with the same size as its source, THE Legacy_Migrator SHALL skip that object and count it as skipped, so that repeated runs leave the same final state.
8. IF a destination object already exists with a size different from its source, THEN THE Legacy_Migrator SHALL leave that destination object unchanged and SHALL record its key as diverged in the result.
9. WHEN copying finishes, THE Legacy_Migrator SHALL verify that every source `raw-posts/*.txt` object and every source `generated-posts/*.md` object has a destination object, and that a destination `learning-status.json` that was copied or skipped equals the source, and SHALL write the result with status, copied count, skipped count, diverged keys, failed keys, and timestamp to `users/{targetUserId}/metadata/migration.json`.
10. WHEN the legacy binding of the Legacy_Owner succeeds, THE Legacy_Owner's `get-learning-status` SHALL report 9 processed posts and 31 chunks, and THE Frontend Results tab of the Legacy_Owner SHALL list the earlier drafts.
11. IF any copy or verification step fails, THEN THE Legacy_Migrator SHALL record status `failed` with the failed keys in `migration.json`, SHALL keep the target User's approved account, and SHALL report the failure in Korean in the `legacyBinding` field of the `admin-approve-user` or `migrate-legacy-data` response.
12. WHEN an Admin_User sends `migrate-legacy-data` with an optional `targetUserId`, THE Legacy_Migrator SHALL run the same idempotent copy and verification for that User, SHALL use the User_Record whose loginId equals LEGACY_OWNER_LOGIN_ID when `targetUserId` is absent, and SHALL return the result.
13. THE Backend SHALL define the constant LEGACY_OWNER_LOGIN_ID with the value `jhe8888`.
14. WHEN an Admin_User approves a pending User whose loginId equals LEGACY_OWNER_LOGIN_ID, THE Auth_Service SHALL set the Account_Status to `active` and, immediately afterwards, SHALL run the Legacy_Migrator once for the userId of that User.
15. THE Auth_Service SHALL process a `register` request for LEGACY_OWNER_LOGIN_ID through the ordinary registration path without special handling.
16. WHEN the Legacy_Migrator starts for a userId, THE Legacy_Migrator SHALL create the Legacy_Binding_Record with that userId if the record is absent, and SHALL proceed when the record already names the same userId.
17. IF the Legacy_Binding_Record names a userId different from the requested target, THEN THE Legacy_Migrator SHALL copy no object and SHALL report the status `already_bound` with the Korean message "레거시 데이터는 이미 다른 사용자에게 연결되어 있습니다."; for `migrate-legacy-data` THE Backend SHALL return HTTP 403 with that message.
18. WHEN `admin-approve-user` completes, THE Auth_Service SHALL include in the response a `legacyBinding` object with `status` (`not_applicable`, `succeeded`, `failed`, or `already_bound`), the copied count, the skipped count, and the failed keys.
19. IF the Legacy_Migrator fails during `admin-approve-user`, THEN THE Auth_Service SHALL keep the Account_Status `active` and SHALL return a Korean failure notice in the `legacyBinding` object.
20. THE MABLOP_System SHALL leave deletion of the original Legacy_Keys and Legacy_Drafts to a separate manual step by the owner after verification, outside this feature.

### Requirement 10: Retirement of the Shared-Token Path

**User Story:** As the system owner, I want the shared token removed once the administrator exists, so that only per-user accounts remain as a way in.

#### Acceptance Criteria

1. WHEN this feature is complete, THE Access_Controller SHALL accept the Bootstrap_Token only for the `bootstrap-admin` action and SHALL return HTTP 401 for any other action that carries the Bootstrap_Token in place of a Session_Token.
2. WHEN this feature is complete, THE Frontend SHALL contain no `TokenField` component, no input field for a Bedrock secret token or any other secret token, and no reference to the localStorage key `mablop-token`; the GrantSelect dropdown of Requirement 21 is permitted.
3. WHEN the Frontend starts, THE Frontend SHALL delete any stored value under the key `mablop-token`.
4. THE MABLOP_System SHALL ship the Backend and Frontend changes of this feature in one release, with no period in which both the shared-token path and the account path are accepted.
5. WHILE no Admin_User exists after deployment, THE Backend SHALL accept only Auth_Exempt_Actions successfully, and the owner SHALL run `bootstrap-admin` immediately after the deployment is confirmed.
6. THE MABLOP_System SHALL leave the SSM parameter that holds the Bootstrap_Token unchanged, and the owner SHALL decide separately whether to delete the parameter.

### Requirement 11: Administrator Account Management

**User Story:** As the administrator, I want to approve registrations and manage accounts, so that only people I trust use MABLOP.

#### Acceptance Criteria

1. WHEN an Admin_User sends `admin-list-users`, THE Auth_Service SHALL return every User with `userId`, `loginId`, `name`, `role`, `status`, `createdAt`, and `approvedAt`, with `pending` Users first and the rest ordered by `createdAt`.
2. WHEN an Admin_User sends `admin-approve-user` for a `pending` User, THE Auth_Service SHALL set the Account_Status to `active`, record `approvedAt`, and return the `legacyBinding` object defined in Requirement 9.
3. WHEN an Admin_User sends `admin-reject-user` for a `pending` User, THE Auth_Service SHALL delete that User_Record and its loginId lookup entry, so that the same loginId can register again.
4. WHEN an Admin_User sends `admin-disable-user` for an `active` member, THE Auth_Service SHALL set the Account_Status to `disabled`.
5. WHEN an Admin_User sends `admin-enable-user` for a `disabled` member, THE Auth_Service SHALL set the Account_Status to `active`.
6. WHEN an Admin_User sends `admin-reset-password` for a member, THE Auth_Service SHALL generate a random temporary password of 16 characters with the `secrets` module, store only its hash, set `credentialsChangedAt` to the current time, clear the failure counter of that loginId, and return the temporary password once in that response.
7. IF an administrator action targets a User whose role is `admin`, THEN THE Auth_Service SHALL return a `bad_request` error with the message "관리자 계정은 변경할 수 없습니다."
8. IF an administrator action targets a userId that has no User_Record, THEN THE Auth_Service SHALL return HTTP 404 with the message "대상 사용자를 찾을 수 없습니다."
9. IF an administrator action requires a status that the target User does not have, THEN THE Auth_Service SHALL return a `bad_request` error with a Korean message that names the required status, and SHALL leave the User_Record unchanged.
10. WHEN a User's Account_Status becomes `disabled` or a User's `credentialsChangedAt` changes, THE Access_Controller SHALL reject that User's earlier Session_Tokens on their next request.

### Requirement 12: User Profile and Password Change

**User Story:** As a logged-in user, I want to see my account information and change my password, so that I can keep my account secure.

#### Acceptance Criteria

1. WHEN the Auth_Service receives `get-me`, THE Auth_Service SHALL return the acting User's `userId`, `loginId`, `name`, `role`, `status`, and `createdAt`.
2. WHEN the Auth_Service receives `change-password` with a correct `currentPassword` and a `newPassword` that satisfies the password policy and differs from `currentPassword`, THE Auth_Service SHALL store the new hash, set `credentialsChangedAt` to the current time, and return a new Session_Token for the acting User.
3. IF `currentPassword` does not match, THEN THE Auth_Service SHALL return a `bad_request` error with the message "현재 비밀번호가 올바르지 않습니다." and SHALL leave the User_Record unchanged.
4. IF `newPassword` violates the password policy or equals `currentPassword`, THEN THE Auth_Service SHALL return a `bad_request` error with a Korean message that states the violated rule.
5. THE Auth_Service SHALL use `bad_request` instead of HTTP 401 for a wrong `currentPassword`, so that the Frontend does not treat the failure as an expired session.

### Requirement 13: Frontend Login, Registration, and Session Handling

**User Story:** As a user, I want a calm login and registration screen and a session that ends cleanly, so that I always know whether I am signed in.

#### Acceptance Criteria

1. WHILE no unexpired Session_Token is stored, THE Frontend SHALL show only the login and registration screen, with the two modes "로그인" and "가입 신청".
2. THE Frontend SHALL provide in login mode the fields "아이디" and "비밀번호", and in registration mode the fields "이름", "아이디", "비밀번호", and "비밀번호 확인".
3. IF the two password fields differ in registration mode, THEN THE Frontend SHALL show "비밀번호가 일치하지 않습니다." and SHALL send no request.
4. WHEN a `register` request succeeds, THE Frontend SHALL show the message returned by the Backend and SHALL return to login mode without logging the User in.
5. WHEN a `login` request succeeds, THE Frontend SHALL store the Session_Token and `expiresAt` in localStorage under the key `mablop-session` and SHALL show the application views.
6. WHEN the Frontend starts with a stored Session_Token whose `expiresAt` has passed, THE Frontend SHALL delete the stored token and show the login screen.
7. WHEN any authenticated request returns HTTP 401, THE Frontend SHALL delete the stored token and show the login screen with the message "로그인이 만료되었습니다. 다시 로그인하세요."
8. WHEN a User selects "로그아웃", THE Frontend SHALL delete the stored Session_Token and show the login screen.
9. THE Frontend SHALL send the stored Session_Token on every request except `login`, `register`, and `bootstrap-admin`.
10. WHILE a login or registration request is in progress, THE Frontend SHALL disable the submit button.
11. THE Frontend SHALL render password fields as masked inputs with the `autocomplete` values `current-password` for login and `new-password` for registration and password change.
12. THE Frontend SHALL show Korean error messages returned by the Backend and SHALL show a Korean fallback message when a request fails without a response.

### Requirement 14: User Page

**User Story:** As a logged-in user, I want one page with my information, my learning status, my password settings, and my Bedrock usage permissions, so that I can manage my account without help.

#### Acceptance Criteria

1. WHILE a User is logged in, THE Frontend SHALL show a navigation entry "내 정보" that opens the user page.
2. THE Frontend SHALL show on the user page the User's "이름" and "아이디" as read-only text, taken from `get-me`.
3. THE Frontend SHALL show on the user page the learning summary taken from `get-learning-status`: "학습한 글" count, "청크" count, and the last learning time in KST.
4. WHEN the learning summary reports 0 processed posts, THE Frontend SHALL show "아직 학습한 글이 없습니다."
5. THE Frontend SHALL provide a password change form with "현재 비밀번호", "새 비밀번호", and "새 비밀번호 확인".
6. WHEN `change-password` succeeds, THE Frontend SHALL replace the stored Session_Token with the returned token, clear the form, and show "비밀번호가 변경되었습니다."
7. THE Frontend SHALL provide a "로그아웃" button on the user page.
8. THE Frontend SHALL provide no account deletion control and no usage statistics on the user page.
9. WHILE the logged-in User's role is `member`, THE Frontend SHALL show on the user page a section "Bedrock 사용 권한" with a request form that has an optional "사용 목적" input limited to 100 characters.
10. WHEN the User submits the request form, THE Frontend SHALL send `grant-request`, SHALL clear the form on success, and SHALL reload the Grant history.
11. THE Frontend SHALL list in the "Bedrock 사용 권한" section the User's Grants from `grant-list-mine`, newest first, each with a status badge "대기", "승인", "거절", "폐기", or "만료", the request time in KST, and, for an approved Grant, the expiry date.
12. WHILE the User already has a Grant with Grant_Status `requested`, THE Frontend SHALL disable the request form and SHALL show the text "승인 대기 중인 신청이 있습니다."
13. WHEN the Grant history is empty, THE Frontend SHALL show "아직 신청한 사용 권한이 없습니다."
14. IF `grant-request` or `grant-list-mine` fails, THEN THE Frontend SHALL show the Korean error message from the Backend inside the section.

### Requirement 15: Administrator Page

**User Story:** As the administrator, I want one page to approve registrations, manage users, and decide Bedrock usage permissions, so that I can handle accounts and Bedrock access in a few clicks.

#### Acceptance Criteria

1. WHILE the logged-in User's role is `admin`, THE Frontend SHALL show a navigation entry "관리" that opens the administrator page.
2. WHILE the logged-in User's role is `member`, THE Frontend SHALL show no "관리" entry and SHALL not render the administrator page.
3. THE Frontend SHALL list pending registrations with "이름", "아이디", and "신청 시각", each with "승인" and "거절" buttons.
4. THE Frontend SHALL list all Users with "이름", "아이디", "상태" (대기, 활성, 비활성), and "가입일".
5. THE Frontend SHALL show "비활성화", "재활성화", and "비밀번호 초기화" controls only for members, with "비활성화" for active members and "재활성화" for disabled members.
6. WHEN the administrator selects "거절", "비활성화", or "비밀번호 초기화", THE Frontend SHALL show an in-page confirmation with "확인" and "취소" before sending the request.
7. WHEN `admin-reset-password` succeeds, THE Frontend SHALL show the temporary password with a copy button and the text "이 비밀번호는 다시 표시되지 않습니다." and SHALL remove the password from the page when the administrator dismisses it or leaves the page.
8. WHEN an administrator action succeeds, THE Frontend SHALL reload the user list.
9. WHEN the list has no pending registrations, THE Frontend SHALL show "대기 중인 가입 신청이 없습니다."
10. IF an administrator action fails, THEN THE Frontend SHALL show the Korean error message from the Backend next to the affected user.
11. WHEN `admin-approve-user` returns a `legacyBinding` with status `failed` or `already_bound`, THE Frontend SHALL show the Korean notice from the response next to the approved user and SHALL keep that user listed as active.
12. WHILE the logged-in User's role is `admin`, THE Frontend SHALL show on the administrator page a section or tab "권한 관리".
13. THE Frontend SHALL list in "권한 관리" the Grants with Grant_Status `requested` with the user's "이름", "아이디", "사용 목적", and "신청 시각", each with a "승인" button, an expiry selector with the choices 7일, 30일, and 90일 (default 30일), and a "거절" button.
14. THE Frontend SHALL list in "권한 관리" the issued Grants with the user's "이름" and "아이디", the status badge ("승인", "거절", "폐기", "만료"), and the expiry date, and SHALL show a "폐기" button for each Grant with Grant_Status `approved` that has not expired.
15. WHEN the administrator selects "거절" or "폐기", THE Frontend SHALL show an in-page confirmation with "확인" and "취소" before sending the request.
16. WHEN a Grant action succeeds, THE Frontend SHALL reload the Grant lists.
17. WHEN no Grant has Grant_Status `requested`, THE Frontend SHALL show "대기 중인 사용 권한 신청이 없습니다."
18. IF a Grant action fails, THEN THE Frontend SHALL show the Korean error message from the Backend next to the affected Grant.

### Requirement 16: Visual Style and Language

**User Story:** As a user, I want the new screens to look calm and consistent with the rest of MABLOP, so that signing in feels simple and trustworthy.

#### Acceptance Criteria

1. THE Frontend SHALL style the login, user, and administrator screens according to the Calm_Design_Guide.
2. THE Frontend SHALL present the login and registration screen as a single centered column with generous whitespace and one primary button per mode, following the calm tone of assistberry.com.
3. THE Frontend SHALL use only `lucide-react` icons and the existing stylesheet conventions, and SHALL add no UI library.
4. THE Frontend SHALL show every label, button, message, and empty state in Korean.
5. THE Backend SHALL write every message of authentication, registration, and administrator responses in Korean, and SHALL keep the English `code` identifiers (`unauthorized`, `forbidden`, `not_found`, `bad_request`, `rate_limited`, `internal_error`) unchanged for programmatic use.
6. THE Frontend SHALL associate each input with a visible label, expose error and status messages with `role="alert"` or `role="status"`, and keep every control operable by keyboard.
7. THE errors module SHALL add the codes `forbidden` (HTTP 403) and `not_found` (HTTP 404) to the existing code-to-status map.

### Requirement 17: Compatibility with Existing Code and Tests

**User Story:** As the developer, I want the existing behavior and test suites preserved except where this feature changes them, so that regressions are visible.

#### Acceptance Criteria

1. THE Access_Controller SHALL keep `check_token(headers, secret)` with its current signature and behavior, and THE Backend SHALL use that function only for `bootstrap-admin`.
2. THE Access_Controller SHALL keep `check_body_length` and `check_rate` unchanged, and `check_request` SHALL replace its pre-shared-token step with the Session_Token verification of Requirement 4.
3. THE handler SHALL keep the names, request fields, and response shapes of the Existing_Actions, except that image keys and draft storage keys now lie under the acting User's User_Prefix, and that the Bedrock_Actions require the body field `grantId` (Requirement 20).
4. THE handler SHALL add the actions `login`, `register`, `bootstrap-admin`, `get-me`, `change-password`, `migrate-legacy-data`, `admin-list-users`, `admin-approve-user`, `admin-reject-user`, `admin-disable-user`, `admin-enable-user`, `admin-reset-password`, `grant-request`, `grant-list-mine`, `admin-grant-list`, `admin-grant-approve`, `admin-grant-reject`, `admin-grant-revoke`, `list-drafts`, and `delete-draft` to `DISPATCH`.
5. THE S3_Store SHALL keep the signatures and behavior of `put_bytes`, `get_bytes`, `put_json`, and `get_json`, and the components SHALL obtain user data keys only from the user-scoped key builder.
6. WHEN this feature is complete, THE backend pytest suite (163 tests before this feature) and THE frontend vitest suite (26 tests before this feature) SHALL pass in full.
7. THE tests changed by this feature SHALL be limited to tests that assert Legacy_Key strings, tests that depend on the Pre_Shared_Token in `check_request` or the handler, tests that call a Bedrock_Action without a `grantId`, tests that expect `bootstrap-admin` to copy legacy data, Frontend tests that depend on `TokenField`, `mablop-token`, or `mablop-last-draft`, Frontend tests of the learning, drafting, and Results views that gain the GrantSelect or the Draft_History_List, and the infra test that lists the allowed S3 prefixes and IAM actions.
8. THE changed tests SHALL keep an equal or stronger assertion for the behavior that they covered, and the task list SHALL name each changed test file.
9. THE MABLOP_System SHALL add tests for: password hash and verify, Session_Token sign, verify, expiry, and tamper detection, the exact Auth_Exempt_Actions set, throttle limits, status transitions, key-builder rejection of invalid userId and path, cross-user isolation, Legacy_Migrator copy-only and idempotent behavior including the Legacy_Binding_Record rules and the `legacyBinding` response, Grant transitions, limits, and expiry, Grant verification on every Bedrock_Action including administrator exemption and immediate revocation, Draft_Index recording, back-fill, `list-drafts` ordering and cap, `delete-draft` ordering, the Backend `preview-learning-sources` action (no S3 write, no Bedrock call, no Grant required), the image marker file name in generated drafts, the Korean message of `rebuild-from-stored` when no raw posts exist, and the Frontend login, user, administrator, GrantSelect, and Draft_History_List views.
10. THE MABLOP_System SHALL add no entry to `backend/requirements.txt` and no dependency to the Frontend `package.json`.

### Requirement 18: Cost and Operational Constraints

**User Story:** As the system owner, I want the feature to cost almost nothing and to change nothing in AWS or production without my approval, so that I stay in control of spend and risk.

#### Acceptance Criteria

1. THE MABLOP_System SHALL implement accounts, Grants, and the Draft_Index using only the existing Lambda, the existing S3 bucket, and SSM Parameter Store, with no additional AWS service.
2. THE Access_Controller SHALL read at most one User_Record from S3 per authenticated request.
3. WHILE usage stays at 2 to 3 Users, THE feature SHALL add less than USD 0.10 per month to AWS charges.
4. THE implementation process SHALL obtain explicit confirmation from the owner before any change is applied to IAM, including the Lambda role policy in `infra/template.yaml` and `infra/lambda-role-policy.json`.
5. THE required IAM changes SHALL be limited to `s3:GetObject` and `s3:PutObject` on `users/*`, `s3:ListBucket` for the `users/` prefix and for the legacy `generated-posts/` prefix, `s3:DeleteObject` on `users/_auth/login/*` and `users/*/account.json` (Requirement 11.3), `s3:DeleteObject` on `users/*/generated-posts/*` (Requirement 22), and `ssm:GetParameter` on the Signing_Secret path, and the implementation SHALL prepare these changes as file edits that remain unapplied until confirmation.
6. THE implementation process SHALL obtain explicit confirmation from the owner before it creates the Signing_Secret SSM parameter, deploys the Backend or Frontend, or runs `bootstrap-admin` against the production environment.
7. THE implementation process SHALL obtain explicit confirmation from the owner before any commit is pushed or merged to `main`, because a push to `main` triggers the `deploy-backend` and `deploy-frontend` workflows.
8. THE implementation process SHALL leave the permissions for the Legacy_Key and Legacy_Draft prefixes in place until the owner confirms that migration is verified, and SHALL obtain confirmation before removing them.
9. THE implementation process SHALL run the test suites locally and in the development environment only, and SHALL not run data-changing commands against the production bucket without confirmation.
10. THE implementation process SHALL confirm that the `s3:prefix` condition of the `s3:ListBucket` statement permits listing `users/` and the legacy `generated-posts/` prefix, and SHALL keep that edit unapplied until the owner confirms it (Criterion 4).
11. THE implementation process SHALL treat the `s3:DeleteObject` statements of Criterion 5 as unapplied file edits until the owner confirms them (Criterion 4).

### Requirement 19: Bedrock Usage Grant Request and Decision

**User Story:** As a member, I want to request permission to use Bedrock, and as the administrator, I want to approve, reject, or revoke that permission with an expiry, so that Bedrock cost arises only for people I approved and no secret string is shared.

#### Acceptance Criteria

1. THE Auth_Service SHALL store the Grants of one User in the Grant_Store, SHALL write the Grant_Store with the S3_Store `update_json` helper, and SHALL generate each grantId randomly with the `secrets` module.
2. THE Auth_Service SHALL store in each Grant the fields `id`, `status`, `purpose`, `requestedAt`, `decidedAt`, and `expiresAt`, and SHALL store no secret value in a Grant.
3. WHEN a User sends `grant-request` with an optional `purpose`, THE Auth_Service SHALL append a Grant with Grant_Status `requested` and `requestedAt` in UTC ISO-8601 format to the Grant_Store of that User and SHALL return the Grant.
4. IF the `purpose` of a `grant-request` is longer than 100 characters, THEN THE Auth_Service SHALL return a `bad_request` error with a Korean message that names the field `purpose` and the limit of 100 characters, and SHALL leave the Grant_Store unchanged.
5. IF the acting User already has a Grant with Grant_Status `requested`, THEN THE Auth_Service SHALL return a `bad_request` error with the message "이미 승인 대기 중인 신청이 있습니다." and SHALL leave the Grant_Store unchanged.
6. WHEN a User sends `grant-list-mine`, THE Auth_Service SHALL return the Grants of the acting User, newest `requestedAt` first, each with a boolean `expired` that is true when the Grant_Status is `approved` and `expiresAt` is earlier than the current time.
7. WHEN an Admin_User sends `admin-grant-list`, THE Auth_Service SHALL return the Grants of all Users, each with `userId`, `loginId`, and `name` of its owner and the boolean `expired`, with Grants in Grant_Status `requested` first and the rest newest `requestedAt` first.
8. WHEN an Admin_User sends `admin-grant-approve` with `userId`, `grantId`, and an optional `expiresInDays`, THE Auth_Service SHALL change a Grant from Grant_Status `requested` to `approved`, set `decidedAt` to the current time, and set `expiresAt` to `expiresInDays` days after `decidedAt`.
9. THE Auth_Service SHALL accept `expiresInDays` values of 7, 30, and 90 and SHALL use 30 when `expiresInDays` is absent.
10. IF `expiresInDays` has any other value, THEN THE Auth_Service SHALL return a `bad_request` error with the message "만료 기간은 7일, 30일, 90일 중에서 선택하세요." and SHALL leave the Grant unchanged.
11. WHEN an Admin_User sends `admin-grant-reject` for a Grant with Grant_Status `requested`, THE Auth_Service SHALL set the Grant_Status to `rejected` and set `decidedAt` to the current time.
12. WHEN an Admin_User sends `admin-grant-revoke` for a Grant with Grant_Status `approved`, THE Auth_Service SHALL set the Grant_Status to `revoked` and set `decidedAt` to the current time.
13. IF an administrator Grant action targets a Grant whose Grant_Status is not the required one, THEN THE Auth_Service SHALL return a `bad_request` error with a Korean message that names the required status, and SHALL leave the Grant unchanged.
14. IF an administrator Grant action names a `grantId` that is absent from the Grant_Store of the target User, THEN THE Auth_Service SHALL return HTTP 404 with the message "대상 권한을 찾을 수 없습니다."
15. IF an administrator Grant action targets a userId that has no User_Record, THEN THE Auth_Service SHALL return HTTP 404 with the message "대상 사용자를 찾을 수 없습니다."
16. THE Access_Controller SHALL treat `admin-grant-list`, `admin-grant-approve`, `admin-grant-reject`, and `admin-grant-revoke` as administrator-only actions under Requirement 4.8.
17. THE Auth_Service SHALL keep at most 20 Grants in one Grant_Store, and WHEN a new Grant would exceed 20, THE Auth_Service SHALL first remove the oldest Grant that has Grant_Status `rejected` or `revoked` or is expired.
18. IF a new Grant would exceed 20 and no Grant is removable, THEN THE Auth_Service SHALL return a `bad_request` error with the message "더 이상 신청할 수 없습니다. 관리자에게 문의하세요." and SHALL leave the Grant_Store unchanged.
19. THE Auth_Service SHALL write every Grant message in Korean and SHALL keep the English `code` identifiers of Requirement 16.5 unchanged.

### Requirement 20: Bedrock Usage Grant Verification

**User Story:** As the system owner, I want the Backend to verify an approved Grant before any action that can call Bedrock, so that an unapproved or expired user cannot cause Bedrock cost.

#### Acceptance Criteria

1. WHEN a User with role `member` sends a Bedrock_Action, THE Backend SHALL require the body field `grantId` and SHALL verify it before the action reads user data or calls Bedrock.
2. THE Backend SHALL accept a `grantId` only when the Grant_Store of the acting User holds a Grant with that id, with Grant_Status `approved` and an `expiresAt` later than the current time.
3. THE Backend SHALL look up the `grantId` only in the Grant_Store of the userId taken from the verified Session_Token, so that a Grant owned by another User is rejected.
4. IF the `grantId` is absent, malformed, unknown, owned by another User, not `approved`, or expired, THEN THE Backend SHALL return HTTP 403 with the code `forbidden` and the identical message "Bedrock 사용 권한이 필요합니다. 마이페이지에서 신청하세요."
5. WHEN an Admin_User revokes a Grant, THE Backend SHALL reject the next Bedrock_Action that carries that `grantId`, because the Backend reads the Grant_Store on each Bedrock_Action and caches no Grant.
6. WHILE the acting User's role is `admin`, THE Backend SHALL skip Grant verification for Bedrock_Actions.
7. THE Backend SHALL read the `grantId` only from the request body, so that the Function URL CORS configuration needs no change.
8. WHEN `get-result` has the `kind` `draft`, THE Backend SHALL perform no Grant verification.
9. THE Backend SHALL perform no Grant verification for `issue-presigned-urls`, `get-learning-status`, `preview-learning-sources`, `list-drafts`, `delete-draft`, and the Grant actions of Requirement 19.
10. THE Backend SHALL read the Grant_Store at most once per Bedrock_Action.

### Requirement 21: Frontend Grant Selector

**User Story:** As a member, I want to choose one of my approved Bedrock permissions on the learning and drafting screens, so that my requests carry a valid permission without typing any secret.

#### Acceptance Criteria

1. WHILE the logged-in User's role is `member`, THE Frontend SHALL show the GrantSelect labeled "Bedrock 사용 권한" on the style-learning view and the drafting view.
2. THE Frontend SHALL list in the GrantSelect only the Grants of `grant-list-mine` that have Grant_Status `approved` and `expired` false.
3. THE Frontend SHALL show each GrantSelect option as "Bedrock 사용 권한 · {approval date} · {expiry date}" with dates in KST in the form `YYYY-MM-DD`.
4. WHEN the User selects a Grant, THE Frontend SHALL store the selected `grantId` in localStorage under the key `mablop-grant-id:{loginId}`.
5. WHEN the Frontend loads the GrantSelect and the stored `grantId` is absent from the listed options, THE Frontend SHALL delete the stored value and show no selection.
6. THE Frontend SHALL send the selected `grantId` in the request body of every Bedrock_Action.
7. WHILE the GrantSelect has no option, THE Frontend SHALL show "사용 가능한 Bedrock 사용 권한이 없습니다. 마이페이지에서 신청하세요." and SHALL disable the buttons that send a Bedrock_Action.
8. IF a Bedrock_Action returns HTTP 403 with the code `forbidden`, THEN THE Frontend SHALL show the Korean message from the Backend and SHALL reload the GrantSelect options.
9. WHILE the logged-in User's role is `admin`, THE Frontend SHALL render no GrantSelect and SHALL send no `grantId`.
10. THE Frontend SHALL associate the GrantSelect with a visible label and keep it operable by keyboard, according to Requirement 16.6.

### Requirement 22: Draft History Recording and Management

**User Story:** As a user, I want every draft I generate recorded in a list that I can reopen and delete, so that I do not lose earlier drafts.

#### Acceptance Criteria

1. WHEN the Drafting_Engine stores a Draft, THE Drafting_Engine SHALL add the entry `{draftId, title, createdAt}` to the Draft_Index of the acting User with the S3_Store `update_json` helper.
2. THE Drafting_Engine SHALL set the entry `title` to the title supplied in the request when that title is not blank, otherwise to the text of the first line of the Draft body that starts with `#` with the leading `#` characters and spaces removed, otherwise to "제목 없음".
3. THE Drafting_Engine SHALL record `createdAt` in UTC ISO-8601 format.
4. IF the Draft_Index update fails after the Draft object is stored, THEN THE Drafting_Engine SHALL return the Draft normally, so that the back-fill in criterion 6 records it later.
5. WHEN a User sends `list-drafts`, THE Backend SHALL read the Draft_Index of the acting User once.
6. WHEN `list-drafts` finds `generated-posts/*.md` objects in the acting User's User_Prefix that the Draft_Index does not list, THE Backend SHALL add an entry for each of them to the Draft_Index, with the title taken from the first line of the body that starts with `#` (or "제목 없음") and `createdAt` taken from the S3 `LastModified` of the object, so that each such object is back-filled once.
7. THE Backend SHALL return from `list-drafts` at most 100 entries of `{draftId, title, createdAt}`, newest `createdAt` first.
8. WHEN a User sends `delete-draft` with a `draftId`, THE Backend SHALL remove the entry from the Draft_Index first and SHALL delete the object `users/{userId}/generated-posts/{draftId}.md` afterwards.
9. IF the `draftId` of a `delete-draft` request contains `/` or `..`, or is empty, THEN THE Backend SHALL return a `bad_request` error with the message "올바르지 않은 초안 번호입니다." and SHALL delete nothing.
10. IF neither a Draft_Index entry nor an object exists for the `draftId` in the acting User's User_Prefix, THEN THE Backend SHALL return HTTP 404 with the message "대상 초안을 찾을 수 없습니다."
11. IF the object deletion of a `delete-draft` request fails after the index update, THEN THE Backend SHALL return `internal_error` with the message of Requirement 6.6, so that a repeated `delete-draft` for the same `draftId` deletes the remaining object.
12. THE Backend SHALL serve the body of a Draft through `get-result` with the `kind` `draft`, read only from the acting User's User_Prefix, as in Requirement 8.5.
13. THE Backend SHALL derive the target User of `list-drafts` and `delete-draft` only from the verified Session_Token, as in Requirement 4.7.
14. WHEN the Legacy_Migrator has copied Legacy_Drafts to a User_Prefix, THE next `list-drafts` of that User SHALL list those Drafts through the back-fill of criterion 6.

### Requirement 23: Frontend Draft History View

**User Story:** As a user, I want the Results tab to show my drafts as a history list like a chat history, so that I can reopen, copy, download, or delete any earlier draft.

#### Acceptance Criteria

1. WHEN the Results tab opens, THE Frontend SHALL send `list-drafts` and SHALL show the Draft_History_List on the left and the body of the selected Draft on the right.
2. THE Frontend SHALL show each Draft_History_List entry with its title, its creation time in KST, and a delete button, ordered newest first.
3. WHEN the Draft_History_List is empty, THE Frontend SHALL show "아직 만든 초안이 없습니다."
4. WHEN the User selects an entry, THE Frontend SHALL fetch the body with `get-result` and the `kind` `draft`, and SHALL show it with a "복사" button and a "다운로드" button that saves the body as a Markdown file.
5. WHEN a Draft has just been generated, THE Frontend SHALL reload the Draft_History_List and SHALL select the new Draft automatically.
6. WHEN the User selects the delete button of an entry, THE Frontend SHALL show an in-page confirmation with "확인" and "취소" before sending `delete-draft`.
7. WHEN `delete-draft` succeeds, THE Frontend SHALL reload the Draft_History_List and, if the deleted Draft was selected, SHALL clear the body view.
8. IF `list-drafts`, `get-result`, or `delete-draft` fails, THEN THE Frontend SHALL show the Korean error message from the Backend next to the affected area.
9. WHILE the viewport is narrower than the single-column breakpoint of the existing stylesheet, THE Frontend SHALL stack the Draft_History_List above the body view.
10. THE Frontend SHALL contain no reference to the localStorage key `mablop-last-draft` or to any key of the form `mablop-last-draft:{loginId}`.
11. WHEN the Frontend starts, THE Frontend SHALL delete every stored localStorage value whose key starts with `mablop-last-draft`.
12. THE Frontend SHALL style the Draft_History_List according to the Calm_Design_Guide, SHALL show every label and message in Korean, and SHALL keep every control operable by keyboard according to Requirement 16.6.
