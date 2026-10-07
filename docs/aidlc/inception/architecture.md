# Architecture (summary)

> Source: `.kiro/specs/mablop-mvp/design.md` (Architecture, Components, Data Model, Interfaces).

## System context

- Browser (React/TS SPA on GitHub Pages) talks to exactly two endpoints:
  - Lambda Function URL (JSON, token-gated).
  - S3 (presigned PUT for image bytes only).
- Bedrock reachable only from inside the Lambda. Path: browser → Lambda → Bedrock.
- All in AWS us-east-1.

## Design principles

1. Browser never touches Bedrock.
2. Images bypass the Lambda body (presigned PUT; keys only to backend).
3. Untrusted content is data, never instructions (delimited DATA fences).
4. Cost is a design constraint (model tiering, caps, compression, alarm).

## Request routing (single Lambda, YAGNI)

One Python Lambda dispatches on `action`:

| action | Component |
|---|---|
| `issue-presigned-urls` | Image_Analyzer |
| `learn-style` | Style_Learner |
| `analyze-images` | Image_Analyzer |
| `generate-draft` | Drafting_Engine |
| `get-result` | Drafting_Engine / Style_Learner |

Access_Controller runs first on every invocation (token, rate, body-length).

## Components

- Access_Controller — token (constant-time), fixed-window rate limit (`metadata/ratelimit.json` + warm memory), body-length cap.
- Style_Learner — URL resolve → crawl/extract → paste fallback → chunk+embed → numpy index → Style_Profile → caps + S3 checkpoint/resume → re-learn overwrites.
- Drafting_Engine — accept inputs → embed → numpy cosine Top_K → load profile → token-capped fenced prompt → quality Korean text-gen → `![img-N]` placeholders → store + synchronous return.
- Image_Analyzer — presigned issuance (scoped `images/`, short expiry, type+size); vision caption once per image lacking description.

## Data model (S3 as the DB)

- Single private, SSE bucket. All metadata JSON.
- `embeddings/index.npy` float32 `[N, D]` + `embeddings/index-map.json`; `vector-index/manifest.json`.
- In-memory numpy cosine `cosine_top_k` (no vector DB).

## Interfaces

- POST JSON to Function URL, header `X-Mablop-Token`.
- Error shape `{"error":{"code","message"}}`; HTTP 401/429/413/502/500.
- Contracts: `issue-presigned-urls`, `learn-style`, `analyze-images`, `generate-draft`, `get-result` (see design.md Interfaces).

## Frontend views

- Style-Learning View, Drafting View, Result/Markdown View (single synchronous loading state).
