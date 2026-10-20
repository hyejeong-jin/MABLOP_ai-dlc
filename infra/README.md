# infra

AWS SAM template for MABLOP MVP. Region: **us-east-1**.

## Stack (`template.yaml`)

- **MablopFunction** ??single Python Lambda (`handler.handler`), routed by `action`.
- **MablopFunctionUrl** ??public Function URL (`AuthType: NONE`); gated at app level by the pre-shared token (Req 5.1). No API Gateway (Req 8.1, 8.5).
- **MablopBucket** ??private S3 bucket, `BlockPublicAccess` fully on, SSE-S3 default encryption (Req 6.4, 8.2).
- **MablopLambdaRole** ??placeholder role (trust policy only). **Task 14.2** slots in the least-privilege policy.
- **MablopBillingAlarm** ??CloudWatch alarm on `AWS/Billing` `EstimatedCharges` (USD) > `MonthlyBudgetUsd` (default 20); notifies **MablopBillingTopic** with an email subscription (`AlarmEmail`). Metric is us-east-1-only = our region (Req 7.6).

## S3 prefixes (Req 8.4)

Prefixes are logical key namespaces, not folders ??S3 creates them on first `PutObject`. No bucket objects are pre-created and no public access is granted. The eight required prefixes:

| Prefix | Holds |
|---|---|
| `raw-posts/` | extracted/pasted post bodies |
| `embeddings/` | `index.npy`, `index-map.json` |
| `vector-index/` | `manifest.json` |
| `style-profile/` | `profile.json` |
| `images/` | uploaded images + caption JSON |
| `generated-posts/` | draft Markdown |
| `prompt-templates/` | instruction templates |
| `metadata/` | checkpoints, rate-limit state, run manifests |

## Secrets

The Lambda reads the pre-shared token from SSM Parameter Store **at runtime** via `ssm:GetParameter` with `WithDecryption=True`. Only the parameter path (`MablopTokenSsmPath`, default `/mablop/token`) is passed to the Lambda as the `MABLOP_TOKEN_SSM_PATH` env var; the token value is never stored in the template, in a Lambda env var, or in source. CloudFormation `ssm-secure` dynamic references are not supported in Lambda environment variables, which is why the value is fetched at runtime (Req 9.3).

## Placeholders

Bedrock model ids (`EmbeddingModelId`, `TextModelId`, `StyleModelId`, `VisionModelId`) and caps (`ContextTokenCap`, `TopK`, `MaxPostCount`) are template parameters with placeholder defaults ??override per deploy.

## Deferred

- **Task 14.2** ??least-privilege IAM policy on `MablopLambdaRole`.

## Deploy

```
sam build && sam deploy --region us-east-1 --guided
```
