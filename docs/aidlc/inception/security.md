# Security (summary)

> Sources: `.kiro/specs/mablop-mvp/design.md` (Security Design) and `.kiro/specs/user-auth-admin/{requirements,design}.md` (authentication, per-user data, Grants).

## Boundaries

- Browser never calls Bedrock; all generation browser → Lambda → Bedrock (R6.1, R6.2).
- Backend is the only Bedrock caller.

## Least-privilege IAM (R6.3)

Lambda role grants only:
- `bedrock:InvokeModel` on enumerated model ARNs (no `bedrock:*`).
- `s3:GetObject`/`s3:PutObject` scoped to the 8 legacy prefixes plus `users/*` (no bucket-wide `s3:*`).
- `s3:ListBucket` with an `s3:prefix` condition: `raw-posts/*`, `users/*`, `generated-posts/*`.
- `s3:DeleteObject` only on `users/_auth/login/*` and `users/u_*` (per-user prefixes; `users/_auth/` is otherwise excluded). No delete permission on any legacy prefix.
- `ssm:GetParameter` on exactly two parameters: the Bootstrap_Token (`/mablop/token`) and the Signing_Secret (`/mablop/signing-secret`).
- logs: `CreateLogStream`/`PutLogEvents` on the Lambda log group only (no account-wide logs).

The delete statement lets the Lambda delete any user's draft or account object, so separation between users rests on application code (see "Per-user data separation"). These IAM changes exist as file edits only and need the owner's confirmation before they are applied.

## Bucket posture (R6.4)

- Private bucket, `BlockPublicAccess` fully on, SSE enabled (SSE-S3 or SSE-KMS).
- Public reach to images only via short-lived presigned URLs.

## Prompt-injection partitioning (R6.5)

- Crawled bodies, user notes, captions = untrusted.
- Placed only inside delimited DATA fences, never in system-instruction region.
- Delimiter sequences in untrusted content escaped/neutralized.

## Presigned URL constraints (R3.2)

- Scoped to the acting user's `users/{userId}/images/` prefix, short expiry (~120s), content-type + max-size conditions.

## Authentication model

Replaces the single shared Pre_Shared_Token with per-user accounts.

- **Public actions**: exactly three can be called without a Session_Token: `login`, `register`, `bootstrap-admin`. Every other action, including an unknown one, requires a valid Session_Token in `X-Mablop-Token`; all failures return the same 401.
- **Registration**: a new account is `pending` and cannot log in until an administrator approves it. The response is the same whether or not the loginId already exists. Pending accounts are capped at 10.
- **Password storage**: PBKDF2-HMAC-SHA256, 600,000 iterations, random 16-byte salt per password, stored with the algorithm and iteration count; compared with `hmac.compare_digest`. Passwords are 10-128 characters. Passwords, hashes, and salts are never returned or logged. An unknown loginId still burns a hash computation so that failures look alike in content and cost.
- **Session_Token**: `base64url(payload).base64url(HMAC-SHA256(secret, payload))`, no padding, payload `v`/`uid`/`iat`/`exp` (epoch ms), lifetime 8 hours, no refresh action, length at most 512. The signature is checked in constant time before the payload is parsed. A token is also rejected when the account is not `active` or when `iat` is earlier than the account's `credentialsChangedAt`, so disabling an account, resetting a password, or changing a password invalidates earlier tokens.
- **Signing_Secret**: SSM SecureString read at runtime (only the path is configured). A missing, unreadable, or shorter-than-32-character secret, or one stored at the Bootstrap_Token path, fails closed with `internal_error`. Rotating it logs everyone out.
- **Bootstrap_Token**: the former shared token, now accepted only by `bootstrap-admin`, which creates the single administrator with an empty workspace. A create-once administrator pointer (`users/_auth/admin.json`) makes a second call fail with 403 before any input is read. The token does not authenticate any other action.
- **Throttling** (state in S3, no new service): 5 failed logins per loginId in 10 minutes block that loginId for 10 minutes, even for a correct password. 30 failures across all loginIds (login, change-password, bootstrap-admin) in 10 minutes block logins and `bootstrap-admin` for 10 minutes. Registration is limited to 5 per hour. Wrong current passwords in `change-password` count as failures. The existing request rate limit and body-length cap stay in front.
- **Roles**: `member` and `admin`. Twelve actions are administrator-only: `migrate-legacy-data`, the seven `admin-*-user` account actions (list, approve, reject, disable, enable, reset password, delete), and four `admin-grant-*` actions (list, approve, reject, revoke). The administrator account itself cannot be changed through the API.
- **Logging**: the audit log records only event, outcome, and the userId of an existing user. It never records passwords, tokens, or the loginId of an unknown account. Unexpected exceptions return a generic message and log only the exception class name.

## Per-user data separation

- The user id comes only from the verified Session_Token; any `userId` in a body is ignored for scoping. All learning data lives under `users/{userId}/`. S3 keys are built by one key builder (`s3store.user_key`) that rejects empty, absolute, `..`, backslash, and control-character paths.
- Image keys supplied by a client must lie under the acting user's `images/` prefix; this is checked before any object is read.
- `get-result` and `delete-draft` resolve ids only inside the acting user's `generated-posts/` prefix, so another user's draft id behaves as "not found".
- Legacy shared objects are not reachable by new users. The Legacy_Migrator only copies them, once, to the approved member `jhe8888` (recorded in `users/_auth/legacy-owner.json`). It never deletes or overwrites originals, and never overwrites a differing destination object.

## Bedrock usage Grants

- A member may call a Bedrock action (`learn-style`, `analyze-images`, `generate-draft`, `rebuild-from-stored`, and `get-result` other than `kind: draft`) only with a `grantId` that names an approved, unexpired Grant in the member's own `grants.json`. Missing, foreign, rejected, revoked, expired, or unknown ids all get the same 403. Administrators are exempt.
- The Backend reads the Grant on every such call and caches nothing, so a revocation applies to the next call.
- A Grant has a requested → approved/rejected → revoked lifecycle, an expiry of 7, 30, or 90 days chosen by the administrator at approval, and no secret value. A user can hold one pending request and at most 20 stored Grants. `expiresAt <= now` counts as expired.
- Preview and status actions that do not call Bedrock (`preview-learning-sources`, `get-learning-status`, `issue-presigned-urls`) need no Grant.

## Secrets / CI (R9)

- OIDC federation, no long-lived keys, no secrets in source or template. Both SSM values (Bootstrap_Token, Signing_Secret) are read at runtime; only their paths are configured.

## Known limitations

- Logout does not revoke the Session_Token. It stays valid until it expires (8 hours), unless the account is disabled or its password changes.
- The Session_Token is stored in `localStorage`, as the old shared token was. The Frontend renders user text through React (no raw HTML), which limits XSS exposure, but GitHub Pages cannot set a Content-Security-Policy header.
- A Grant has no usage quota. An approved Grant allows unlimited Bedrock calls until it expires or is revoked. A `learn-style` run that started before a revocation runs to its end.
- Failure counters are per loginId and global, not per client address. A client that sends 5 bad passwords every 10 minutes can keep one loginId blocked; 30 bad attempts block all logins for 10 minutes. Blocks expire on their own and `admin-reset-password` clears the per-loginId counter.
- The delete permission covers all users' drafts and account objects; only application code separates users.
- Rotating the Signing_Secret logs everyone out.
- Copied legacy drafts show the copy time, not the original creation time.
- A failed legacy binding can be retried only through the API (`migrate-legacy-data`), and only for the user named in the binding record.
