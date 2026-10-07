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
| Text generation (quality Korean) | `anthropic.claude-3-5-haiku-20241022-v1:0` | fast, strong Korean, far cheaper than Sonnet | ~$0.80 in / $4.00 out per 1M (confirm live) |
| Style profile / proofing (cheap) | `anthropic.claude-3-haiku-20240307-v1:0` | cheapest Claude, fine for structured JSON | ~$0.25 in / $1.25 out per 1M (confirm live) |
| Image captioning (low/mid vision) | `anthropic.claude-3-haiku-20240307-v1:0` | multimodal, cheapest vision-capable; short captions | billed as Claude 3 Haiku + image tokens |

Sources: Amazon Bedrock pricing page; Titan Text Embeddings V2 + Claude 3/3.5 Haiku model cards (aws docs). Prices change ? treat the table as the committed selection, re-check the live pricing page at deploy. Bedrock remains the main variable cost; infra stays <$1/mo.

These IDs are wired as `infra/template.yaml` parameter defaults (EmbeddingModelId / TextModelId / StyleModelId / VisionModelId) and as the backend module fail-safe defaults.

## Resolved deploy blockers

- IAM policy placeholders renamed to match template params: `${TextModelId}`, `${StyleModelId}` (were `${KoreanTextModelId}` / `${StyleProofModelId}`).
- Template env var corrected to `MABLOP_EMBED_MODEL` (backend reads that, not `MABLOP_EMBEDDING_MODEL`).
- `backend/pyproject.toml` now lists `[tool.setuptools] py-modules` explicitly (flat-layout packaging fixed).


## Deploy blockers (found during testing consolidation)

- **Bedrock model-id placeholder naming mismatch.** `infra/lambda-role-policy.json` uses
  `${EmbeddingModelId}` / `${KoreanTextModelId}` / `${StyleProofModelId}` / `${VisionModelId}`,
  while `infra/template.yaml` parameters are `EmbeddingModelId` / `TextModelId` / `StyleModelId`
  / `VisionModelId`. Reconcile these names (and substitute real us-east-1 model ARNs) before
  the first `sam deploy`, together with locking the model IDs in the "Open / to-confirm" section above.
- **backend/pyproject.toml flat-layout.** `pip install -e` fails due to setuptools multi-top-level
  ambiguity; tests run via the conftest sys.path shim. Add an explicit `[tool.setuptools] py-modules`
  (or packages) list before packaging the Lambda deployment artifact.

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
