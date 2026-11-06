# infra

AWS SAM template for MABLOP MVP. Region: **us-east-1**.

## Stack (`template.yaml`)

- **MablopFunction** - single Python Lambda (`handler.handler`), routed by `action`.
- **MablopFunctionUrl** - public Function URL (`AuthType: NONE`). Access is gated at app level by per-user Session_Tokens (HMAC-signed, 8 hours). Only `login`, `register`, and `bootstrap-admin` are callable without a Session_Token. No API Gateway.
- **MablopBucket** - private S3 bucket, `BlockPublicAccess` fully on, SSE-S3 default encryption.
- **MablopLambdaRole** - least-privilege execution role (policy mirrored in `lambda-role-policy.json`).
- **MablopBillingAlarm** - CloudWatch alarm on `AWS/Billing` `EstimatedCharges` (USD) > `MonthlyBudgetUsd` (default 20); notifies **MablopBillingTopic** with an email subscription (`AlarmEmail`). Metric is us-east-1-only = our region.

No resource type was added by the user-auth-admin feature. Accounts, Grants, and the draft index are JSON objects in the existing bucket.

## Parameters and environment variables

| Parameter | Default | Lambda env var | Holds |
|---|---|---|---|
| `MablopTokenSsmPath` | `/mablop/token` | `MABLOP_TOKEN_SSM_PATH` | Path of the Bootstrap_Token (the former shared token). Used only by `bootstrap-admin`. |
| `MablopSigningSecretSsmPath` | `/mablop/signing-secret` | `MABLOP_SIGNING_SECRET_SSM_PATH` | Path of the Signing_Secret that signs Session_Tokens. New. |

Only the paths are in the template. Neither value is stored in the template, in a Lambda env var, or in source. The Lambda fetches both at runtime with `ssm:GetParameter` and `WithDecryption=True` (CloudFormation `ssm-secure` references are not supported in Lambda environment variables). The two paths must differ, and the signing secret must be at least 32 characters, otherwise the Lambda fails closed with `internal_error`.

Bedrock model ids (`EmbeddingModelId`, `TextModelId`, `StyleModelId`, `VisionModelId`) and caps (`ContextTokenCap`, `TopK`, `MaxPostCount`) are unchanged template parameters.

## S3 layout

Prefixes are logical key namespaces, not folders. S3 creates them on first `PutObject`. No bucket objects are pre-created.

| Prefix | Holds |
|---|---|
| `users/{userId}/` | All per-user data: `account.json`, `raw-posts/`, `embeddings/`, `vector-index/`, `style-profile/`, `images/`, `generated-posts/`, `metadata/` (learning status, `grants.json`, `drafts.json`, checkpoints, `migration.json`) |
| `users/_auth/` | `login/{loginId}.json` lookups, the create-once administrator pointer `admin.json`, the login throttle state `throttle.json`, and the legacy binding record `legacy-owner.json` |
| `metadata/ratelimit.json` | Global request rate-limit state (outside any user prefix) |
| Legacy: `raw-posts/`, `embeddings/`, `vector-index/`, `style-profile/`, `images/`, `generated-posts/`, `prompt-templates/`, `metadata/` | The original shared data. The Legacy_Migrator only copies from here to the approved `jhe8888` user. The originals are never deleted or overwritten by the application. |

## IAM changes (prepared, not applied)

These are edits to `template.yaml` and the mirror `lambda-role-policy.json`. The role policy is applied by `sam deploy`, which runs when something is pushed to `main`.

| Statement (Sid) | Change |
|---|---|
| `S3Prefixes` | `s3:GetObject` / `s3:PutObject` now also on `users/*` (nine prefixes: the eight legacy ones plus `users`). |
| `ListRawPosts` | `s3:ListBucket` with `s3:prefix` condition `raw-posts/*`, `users/*`, `generated-posts/*` (was `raw-posts/*` only). The Sid is unchanged, so the name is narrower than the statement now. |
| `DeleteUserObjects` (new) | `s3:DeleteObject` on `users/_auth/login/*`, `users/*/account.json`, `users/*/generated-posts/*` only. No delete permission on any legacy prefix. |
| `ReadToken` | `ssm:GetParameter` on exactly two parameters: the Bootstrap_Token path and the Signing_Secret path. |
| Unchanged | Bedrock invoke, logs, Function URL CORS (`Content-Type`, `X-Mablop-Token`), bucket CORS, billing alarm, resource types. |

Review note: `DeleteUserObjects` lets the Lambda delete any user's draft and account object. Isolation between users is enforced by application code (every key is built from the user id in the Session_Token). If the owner declines the `account.json` and lookup patterns, `admin-reject-user` needs a tombstone fallback. `delete-draft` has no fallback.

## Confirm with the owner before applying

Nothing in this folder has been applied to any environment. Each item needs the owner's explicit confirmation first:

1. **IAM**: review and apply the policy changes above (via `sam deploy` or `aws iam put-role-policy`).
2. **SSM**: create the SecureString `/mablop/signing-secret` (32+ random characters). CloudFormation does not create it.
3. **Deploy**: push to `main` runs `deploy-backend` and `deploy-frontend`. Confirm before pushing.
4. **Production `bootstrap-admin`**: run once, right after the deployment, with the Bootstrap_Token.
5. **Approving `jhe8888`**: the approval triggers the one-time legacy data copy. Verify afterwards (9 posts, 31 chunks, earlier drafts).

The step-by-step procedure is in `docs/aidlc/operations/deploy-runbook.md` (section 10).

## Deploy

CI: push to `main` (see above). Manual:

```
sam build --template infra/template.yaml
sam deploy --region us-east-1 --stack-name mablop --resolve-s3 --capabilities CAPABILITY_IAM
```

On Windows, `sam build` can fail from a path that contains non-ASCII characters. Build from an ASCII staging copy such as `C:\mablop-deploy`.
