# AI-DLC State

- Phase: Construction COMPLETE ? deploy-ready (not yet deployed).
- Prior: Inception complete ? architecture, cost model, security locked.

## Status
- Backend (Python Lambda): complete. 159 tests pass (unit + 17 tagged property tests).
- Frontend (React/TS): complete. 26 tests pass (component + no-SDK static guard).
- IaC (SAM) + CI/CD (GitHub Actions OIDC): authored + deploy-fixed. 17 config-snapshot checks pass.
  (least-privilege policy now inlined into the role; Function URL invoke permission + log group added.)
- Deploy runbook: docs/aidlc/operations/deploy-runbook.md (turnkey, OIDC, no keys in repo).
- OIDC role policies: infra/github-oidc/{trust-policy,deploy-permissions}.json (placeholders to fill).
- Bedrock model IDs: locked (decisions.md D14), wired as template params + backend defaults.
- `pip install -e` / Lambda packaging: fixed (pyproject py-modules).

## Locked decisions
- S3-only persistence; numpy cosine search.
- Single Python Lambda Function URL; Amazon Bedrock (Titan Embed v2 / Claude 3.5 Haiku / Claude 3 Haiku).
- React/TS SPA on GitHub Pages. Region: us-east-1.

## Remaining before go-live (require live AWS account; not done here)
- Enable Bedrock model access for the 3 model IDs in the target account/region.
- Set SSM SecureString /mablop/token, GitHub repo vars (AWS_DEPLOY_ROLE_ARN, VITE_MABLOP_API_URL, VITE_BASE_PATH), AlarmEmail.
- Create the GitHub OIDC IAM role; run deploy-backend then deploy-frontend workflows.
- Confirm live Bedrock pricing vs decisions.md D14 table.