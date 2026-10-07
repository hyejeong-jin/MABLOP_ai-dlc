# Security (summary)

> Source: `.kiro/specs/mablop-mvp/design.md` (Security Design).

## Boundaries

- Browser never calls Bedrock; all generation browser → Lambda → Bedrock (R6.1, R6.2).
- Backend is the only Bedrock caller.

## Least-privilege IAM (R6.3)

Lambda role grants only:
- `bedrock:InvokeModel` on enumerated model ARNs (no `bedrock:*`).
- `s3:GetObject`/`s3:PutObject` scoped to the 8 defined prefixes (no bucket-wide `s3:*`).
- logs: `CreateLogStream`/`PutLogEvents` on the Lambda log group only (no account-wide logs).

## Bucket posture (R6.4)

- Private bucket, `BlockPublicAccess` fully on, SSE enabled (SSE-S3 or SSE-KMS).
- Public reach to images only via short-lived presigned URLs.

## Prompt-injection partitioning (R6.5)

- Crawled bodies, user notes, captions = untrusted.
- Placed only inside delimited DATA fences, never in system-instruction region.
- Delimiter sequences in untrusted content escaped/neutralized.

## Presigned URL constraints (R3.2)

- Scoped to `images/` prefix, short expiry (~120s), content-type + max-size conditions.

## Access control (R5)

- Pre_Shared_Token required (constant-time compare), rate limit, body-length cap.

## Secrets / CI (R9)

- OIDC federation, no long-lived keys, no secrets in source. Token from Lambda env var.
