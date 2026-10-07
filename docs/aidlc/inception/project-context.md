# Project Context

> Living doc. Detail lives in `.kiro/specs/mablop-mvp/`.

## What

- MABLOP = My Blog Posting Agent.
- Personal AI blog-drafting SaaS for 2-3 trusted users, intermittent use.
- Learns a user's Korean writing style from their Naver blog.
- Generates style-matched Markdown drafts via RAG.

## Who

- 2-3 trusted users.
- Single system owner (ops + access control).

## Top constraint

- Minimize operating cost. Infra target USD 5-20/month.
- Bedrock billed separately = main variable cost.

## Approach

- YAGNI. Minimum AWS surface.
- S3-only storage (no DB). numpy cosine search (no vector DB).
- Single public Lambda Function URL (Python, us-east-1).
- React/TS SPA on GitHub Pages.
- Bedrock for embedding / text-gen / vision.

## Features (MVP)

- FR-1 blog style learning (Style_Learner).
- FR-2 AI blog drafting (Drafting_Engine).
- FR-3 image context-aware placement (Image_Analyzer).
- Draft export as Markdown.
- Access control, security, cost control, CI/CD, AI-DLC docs.
