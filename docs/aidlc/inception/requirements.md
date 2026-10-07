# Requirements (summary)

> Source of truth: `.kiro/specs/mablop-mvp/requirements.md`. Below is the condensed index.

- R1 Blog Style Learning — crawl Naver (m.blog/iframe body), paste fallback, chunk+embed, numpy Vector_Index, Style_Profile, MAX_POST_COUNT cap, S3 checkpoint for 15-min timeout, manual re-learn overwrites.
- R2 AI Blog Drafting — title/outline/notes + up to 10 images, embed input, Top_K cosine retrieval, load Style_Profile, token-capped few-shot prompt, Bedrock text-gen, synchronous return.
- R3 Image Context-Aware Placement — presigned PUT to `images/` (keys only to backend), vision caption only when user description absent, `![img-N]` placeholders.
- R4 Draft Output & Storage — Markdown with `![img-N]`, stored under `generated-posts/`, all metadata as JSON.
- R5 Access Control — Pre_Shared_Token required, rate limit, body-length limit.
- R6 Security — browser never calls Bedrock; least-privilege IAM; private + SSE buckets; untrusted-content partitioning.
- R7 Cost Control — context token cap, Top_K cap, image resize/compress, 10-image cap, short captions, billing alarm.
- R8 Infrastructure Constraints — us-east-1, S3-only, numpy cosine, 8 fixed prefixes, excluded services list.
- R9 CI/CD & Hosting — GitHub Actions, OIDC (no long-lived keys), no secrets in code, GitHub Pages.
- R10 AI-DLC Docs — maintain inception/construction/operations docs + state.md + backlog.md.

## Excluded (R8.5)

API Gateway, DynamoDB, Cognito, Step Functions, Bedrock Agent/KB, OpenSearch, RDS, EC2/ECS/Fargate/Kubernetes, headless browser crawling.

## S3 prefixes (R8.4)

`raw-posts/` `embeddings/` `vector-index/` `style-profile/` `images/` `generated-posts/` `prompt-templates/` `metadata/`
