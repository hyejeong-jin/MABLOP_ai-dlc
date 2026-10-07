# MABLOP_ai-dlc

MABLOP = My Blog Posting Agent. 사용자의 블로그 문체를 학습하여 사용자를 대신해 블로그 초안을 작성하는 개인 맞춤형 AI 블로그 작성 서비스.

소규모(신뢰 사용자 2~3명) 개인용. 네이버 블로그에서 한국어 문체를 학습하고 RAG로 문체를 재현한 Markdown 초안을 생성합니다.

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

## 배포
`docs/aidlc/operations/deploy-runbook.md` 참고 (GitHub OIDC, 장기 키 없음).