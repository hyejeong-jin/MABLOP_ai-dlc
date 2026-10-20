# Decisions (locked from Inception)

> Rationale detail in `.kiro/specs/mablop-mvp/design.md`.

- D1 S3-only persistence, no database (R8.2).
- D2 numpy cosine similarity, no FAISS/OpenSearch/Pinecone/Weaviate (R8.3).
- D3 Single public Lambda Function URL routed by `action`, no API Gateway (R8.1, R8.5).
- D4 React/TS SPA on GitHub Pages = only frontend host (R9.4).
- D5 Bedrock = only ML service (embedding / text-gen / vision).
- D6 Region = us-east-1 (R8.1).
- D7 Browser ??Lambda ??Bedrock only; browser never calls Bedrock (R6.1, R6.2).
- D8 Images via presigned PUT to `images/`; backend receives keys only (R3.1).
- D9 Untrusted content fenced in delimited DATA sections (R6.5).
- D10 Bedrock model tiering: cheap multilingual embedding, quality Korean text-gen, cheap style/proof, low/mid vision once per image (R7, cost).
- D11 Access gating: Pre_Shared_Token + rate limit + body-length cap (R5).
- D12 CI/CD via GitHub Actions with OIDC; no long-lived keys, no secrets in code (R9).
- D13 Excluded services: API Gateway, DynamoDB, Cognito, Step Functions, Bedrock Agent/KB, OpenSearch, RDS, EC2/ECS/Fargate/Kubernetes, headless browser crawling (R8.5).

## D14 Locked Bedrock model IDs (us-east-1)

Resolved from AWS docs/pricing (verify live console access per account before first deploy; some models require access enablement):

| Tier | Model ID | Why | Approx us-east-1 price |
|---|---|---|---|
| Embedding (cheap multilingual) | `amazon.titan-embed-text-v2:0` | 1024-dim, 100+ langs incl. Korean, cheapest | ~$0.02 / 1M input tokens |
| Text generation (quality Korean) | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | quality-focused Korean generation | confirm live pricing |
| Style profile / proofing (fallback) | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | verified account access; revisit Haiku when available | confirm live pricing |
| Image captioning (low/mid vision) | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | multimodal fallback; short captions | Sonnet 4.5 image-token pricing; confirm live |

Sources: Amazon Bedrock pricing page; Titan Text Embeddings V2 + Claude Sonnet 4.5 / Haiku 5.5 model cards (aws docs). Prices change ? treat the table as the committed selection, re-check the live pricing page at deploy. Bedrock remains the main variable cost; infra stays <$1/mo.

These IDs are wired as `infra/template.yaml` parameter defaults (EmbeddingModelId / TextModelId / StyleModelId / VisionModelId) and as the backend module fail-safe defaults.

## Resolved deploy blockers

- IAM policy placeholders renamed to match template params: `${TextModelId}`, `${StyleModelId}` (were `${KoreanTextModelId}` / `${StyleProofModelId}`).
- Template env var corrected to `MABLOP_EMBED_MODEL` (backend reads that, not `MABLOP_EMBEDDING_MODEL`).
- `backend/pyproject.toml` now lists `[tool.setuptools] py-modules` explicitly (flat-layout packaging fixed).


## Resolved deploy blockers

- IAM policy placeholders match the SAM parameter names.
- The Lambda embedding environment variable is `MABLOP_EMBED_MODEL`.
- Backend flat-layout packaging is fixed with explicit `py-modules`.
- The Lambda role policy is inlined in `infra/template.yaml`.
- Function URL permission and explicit log group are present.

## Correctness fixes made during property-based testing

- `access_controller.check_token`: encode token operands to UTF-8 bytes before
  `hmac.compare_digest` (non-ASCII token raised TypeError -> 500 instead of 401). Caught by Property 14.
- `prompt_builder.build_prompt`: last-resort truncation now treats `cap * CHARS_PER_TOKEN` as an
  absolute char ceiling (tiny caps previously exceeded the budget via the trim marker). Caught by Property 7.
- `retrieval.cosine_top_k`: clamp zero norms instead of adding `1e-12` so cosine is exactly
  scale-invariant for tiny-magnitude vectors. Caught by Property 6 (scale-invariance).

## Known edge cases (noted, not yet hardened)

- `index_store.build_and_store_index` raises on an empty vector set (N=0) via `reshape(0, -1)`.
- `retrieval.cosine_top_k` with `k=0` would index `argpartition(-1)`; callers pass k>=1.


## Account availability note

Claude Haiku 5.5 returned `not available for this account` in the Bedrock console. Sonnet 4.5 succeeded in the same account and region, so MVP style analysis and image captioning use Sonnet 4.5 until Haiku access is granted.
