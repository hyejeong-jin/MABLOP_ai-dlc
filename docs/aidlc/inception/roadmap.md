# Roadmap (dev phases)

> Source: `.kiro/specs/mablop-mvp/tasks.md`. Phases ordered to match AI-DLC; frontend sequenced after backend contracts fix.

- Phase 2 — Repo bootstrap + AI-DLC inception docs.
- Phase 4 — Lambda backend core: routing, Access_Controller, S3 helper, error shapes.
- Phase 5 — Naver crawler + paste fallback (Style_Learner part 1).
- Phase 6 — Embedding pipeline + `learn-style` wiring (Style_Learner part 2).
- Phase 7 — RAG retrieval + prompt construction (Drafting_Engine part 1).
- Phase 8 — Post generation + image analysis (Image_Analyzer + Drafting_Engine part 2). **Gate: Bedrock model IDs + pricing locked in decisions.md first.**
- Phase 3 — Frontend (style-learning, drafting, result views).
- Phase 9 — Testing consolidation (17 properties, frontend component tests).
- Phase 10 — Deploy / IaC + CI/CD (Lambda, Function URL, SSE bucket, least-privilege IAM, billing alarm, GitHub Actions OIDC, Pages).

Checkpoints between phases enforce incremental validation.
