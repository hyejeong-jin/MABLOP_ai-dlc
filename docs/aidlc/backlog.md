# AI-DLC Backlog

All spec task groups complete (see .kiro/specs/mablop-mvp/tasks.md ? 71/71 leaf tasks done).

- [x] Phase 2 ? Repo bootstrap + inception docs
- [x] Phase 4 ? Lambda backend core (routing, access control, S3 helper, errors)
- [x] Phase 5 ? Naver crawler + paste fallback (Style_Learner pt1)
- [x] Phase 6 ? Embedding pipeline + learn-style wiring (Style_Learner pt2)
- [x] Phase 7 ? RAG retrieval + prompt construction (Drafting_Engine pt1)
- [x] Phase 8 ? Post generation + image analysis (Image_Analyzer + Drafting_Engine pt2)
- [x] Phase 3 ? Frontend (style-learning / drafting / result views)
- [x] Phase 9 ? Testing consolidation (17 property tests, frontend component tests)
- [x] Phase 10 ? Deploy / Infra (IaC) + CI/CD
- [x] Deploy-readiness ? Bedrock model IDs locked, IAM/template params reconciled, packaging fixed

## Go-live checklist (needs a live AWS account ? out of scope for the repo)
- [ ] Enable Bedrock access for titan-embed-text-v2:0, claude-3-5-haiku, claude-3-haiku (us-east-1)
- [ ] Set SSM /mablop/token + GitHub repo vars/secrets (AWS_DEPLOY_ROLE_ARN, VITE_MABLOP_API_URL, VITE_BASE_PATH, AlarmEmail)
- [ ] Create GitHub OIDC deploy role; attach lambda-role-policy.json to the Lambda role
- [ ] Run deploy-backend, then deploy-frontend; smoke-test learn-style + generate-draft
- [ ] Re-check live Bedrock pricing vs decisions.md D14

## Known edge cases (noted, not hardened)
- index_store.build_and_store_index raises on empty vector set (N=0).
- retrieval.cosine_top_k assumes k>=1.