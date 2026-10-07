# MABLOP

MABLOP = My Blog Posting Agent. Personal, very-small-scale AI blog-drafting tool for 2-3 trusted users. Learns a Korean writing style from a Naver blog and generates style-matched Markdown drafts via RAG.

## Stack
- S3 only (no DB), numpy cosine vector search
- Single public Lambda Function URL (Python) + Amazon Bedrock
- React/TypeScript SPA on GitHub Pages
- Region: us-east-1

## Layout
- `frontend/` — React + TS SPA
- `backend/` — Python Lambda (single handler + components)
- `infra/` — IaC: Lambda, Function URL, S3, IAM, billing alarm
- `.github/workflows/` — GitHub Actions (OIDC)
- `docs/aidlc/` — AI-DLC deliverables (inception/construction/operations, state.md, backlog.md)
