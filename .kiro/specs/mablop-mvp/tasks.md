# Implementation Plan: MABLOP MVP

## Overview

This plan builds MABLOP incrementally, ordered to match the AI-DLC development phases. Each phase builds on the previous one and ends with wiring the new code into an integrated whole, so there is no orphaned code. Backend is Python (single Lambda handler routed by `action`, pytest + moto + botocore Stubber); frontend is React/TypeScript (GitHub Pages). Persistence is S3 only; vector search is numpy cosine. YAGNI is honored: no services beyond S3, Lambda Function URL, Bedrock, and GitHub Pages are introduced.

Property-based test (PBT) sub-tasks are marked optional with `*`, run ??100 iterations, and are tagged `Feature: mablop-mvp, Property N`. IaC/config snapshot verification tasks are called out explicitly and are not PBT. Each sub-task references the specific requirement clauses and design sections/properties it implements.

## Tasks

- [x] 1. Phase 2 ??Repository bootstrap and AI-DLC inception docs
  - [x] 1.1 Scaffold the repository structure
    - Create `frontend/`, `backend/`, `infra/`, `.github/workflows/`, and `docs/aidlc/{inception,construction,operations}/` directories
    - Create `docs/aidlc/state.md` and `docs/aidlc/backlog.md` seeded from the spec
    - Add top-level `README.md` and `.gitignore` for Python + Node artifacts
    - _Requirements: 10.1, 10.3_
    - _Design: Repository Structure (incl. AI-DLC deliverables)_

  - [x] 1.2 Write the AI-DLC inception documents seeded from the spec
    - Create `docs/aidlc/inception/project-context.md`, `requirements.md`, `architecture.md`, `decisions.md`, `cost-model.md`, `security.md`, `roadmap.md`
    - Seed `architecture.md` from the design Architecture/Components sections; `cost-model.md` from Cost Design; `security.md` from Security Design; `requirements.md` from the spec requirements
    - _Requirements: 10.1, 10.2_
    - _Design: Repository Structure; Cost Design; Security Design_

  - [x] 1.3 Initialize the Python backend project
    - Create `backend/` Python package with module stubs: `access_controller.py`, `style_learner.py`, `drafting_engine.py`, `image_analyzer.py`, and `handler.py`
    - Add `pyproject.toml`/`requirements.txt` with `boto3`, `numpy`, `pytest`, `moto`, and a PBT library (`hypothesis`)
    - Create `backend/tests/` with a `conftest.py` and a passing smoke test; configure the `property` test tag/marker
    - _Requirements: 8.2, 8.3_
    - _Design: Request Routing (single Lambda, YAGNI); Testing Strategy_

  - [x] 1.4 Initialize the React/TypeScript frontend project
    - Scaffold `frontend/` (Vite + React + TS) with view folders `src/views/` for style-learning, drafting, result
    - Add a component test runner (Vitest + Testing Library) with a passing smoke test
    - Add a central API client module stub targeting the Lambda Function URL (base URL via env)
    - _Requirements: 9.4_
    - _Design: Frontend view structure (React/TypeScript)_

- [x] 2. Checkpoint ??repo scaffold builds
  - Ensure the backend test runner and frontend test runner both execute cleanly; ask the user if questions arise.

- [x] 3. Phase 4 ??Lambda backend core (routing, access control, S3 helper, errors)
  - [x] 3.1 Implement the error-shape helpers and HTTP status mapping
    - Add a module producing the common error body `{"error": {"code","message"}}` and mapping to HTTP `401/429/413/502/500`
    - _Requirements: 5.1, 5.3, 5.4_
    - _Design: Interfaces (Lambda request/response contracts); Error Handling_

  - [x] 3.2 Implement the S3 helper with JSON metadata round-trip
    - Add get/put helpers over the single private bucket keyed by prefix; JSON serialize/deserialize for metadata objects
    - _Requirements: 4.4, 8.2, 8.4_
    - _Design: Data Model (S3 as the database)_

  - [x]* 3.3 Write property test for metadata JSON round-trip
    - **Property 13: Metadata round-trips as JSON** ??tagged `Feature: mablop-mvp, Property 13`, ??100 iterations, S3 mocked via moto
    - **Validates: Requirements 4.4**

  - [x] 3.4 Implement Access_Controller token, rate, and body-length checks
    - Constant-time compare of `X-Mablop-Token` against the env-sourced secret ??`401` on miss (5.1, 5.2)
    - Fixed-window rate counter backed by `metadata/ratelimit.json` + warm module-global memory ??`429` over rate (5.3)
    - Reject when decoded body / `Content-Length` exceeds the cap ??`413` before parse (5.4)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 9.3_
    - _Design: Access_Controller (Req 5)_

  - [x]* 3.5 Write property test for token validation
    - **Property 14: Token validation accepts only the exact secret** ??tagged `Feature: mablop-mvp, Property 14`, ??100 iterations
    - **Validates: Requirements 5.1, 5.2**

  - [x]* 3.6 Write property test for rate limiting
    - **Property 15: Rate limiting bounds requests per window** ??tagged `Feature: mablop-mvp, Property 15`, ??100 iterations
    - **Validates: Requirements 5.3**

  - [x]* 3.7 Write property test for body-length limit
    - **Property 16: Body-length limit rejects oversized bodies** ??tagged `Feature: mablop-mvp, Property 16`, ??100 iterations
    - **Validates: Requirements 5.4**

  - [x] 3.8 Implement the single Lambda handler with action routing
    - `handler.py` runs Access_Controller first, then dispatches on `action` (`issue-presigned-urls`, `learn-style`, `analyze-images`, `generate-draft`, `get-result`) to component stubs; returns the common error shape on failure
    - _Requirements: 5.2, 6.2, 8.1, 8.5_
    - _Design: Request Routing (single Lambda, YAGNI)_

- [x] 4. Checkpoint ??backend core wired
  - Ensure routing + access-control tests pass; ask the user if questions arise.

- [x] 5. Phase 5 ??Naver crawler and paste fallback (Style_Learner part 1)
  - [x] 5.1 Implement Naver URL resolution to m.blog / PostView
    - Pure function: normalize submitted URL to `m.blog.naver.com` or extract `blogId`/`logNo` and build the direct PostView body URL; no network
    - _Requirements: 1.1_
    - _Design: Style_Learner step 1 (URL resolution)_

  - [x]* 5.2 Write unit tests for URL resolution
    - Table-driven tests over real Naver URL-shape fixtures (desktop, mobile, iframe)
    - _Requirements: 1.1_

  - [x] 5.3 Implement HTTP crawl and HTML?�text extraction
    - Plain `urllib`/`requests` GET (no headless browser); extract post body text; store raw bodies under `raw-posts/`
    - Surface per-post crawl failures for the paste fallback
    - _Requirements: 1.2, 1.7_
    - _Design: Style_Learner step 2 (crawl + extract); Error Handling (crawl fails)_

  - [x]* 5.4 Write unit tests for HTML?�text extraction
    - Assert extracted text contains no markup leakage; mock HTTP responses with fixtures
    - _Requirements: 1.2_

  - [x] 5.5 Implement paste-text fallback
    - Accept `pastedTexts[]` from the `learn-style` payload and use pasted body for posts whose crawl failed; route both into the same downstream pipeline
    - _Requirements: 1.3_
    - _Design: Style_Learner step 3 (paste fallback)_

  - [x] 5.6 Implement the MAX_POST_COUNT cap
    - Cap discovered posts to `min(len(urls), MAX_POST_COUNT)` per run
    - _Requirements: 1.8_
    - _Design: Style_Learner step 7 (caps)_

  - [x]* 5.7 Write property test for the post-count cap
    - **Property 3: Post-count cap is never exceeded** ??tagged `Feature: mablop-mvp, Property 3`, ??100 iterations
    - **Validates: Requirements 1.8**

- [x] 6. Phase 6 ??Embedding pipeline and learn-style wiring (Style_Learner part 2)
  - [x] 6.1 Implement chunking of post bodies into Post_Chunks
    - Fixed target size with small overlap; non-empty chunks; full coverage of the body
    - _Requirements: 1.4_
    - _Design: Style_Learner step 4 (chunk + embed)_

  - [x]* 6.2 Write property test for chunking invariants
    - **Property 1: Chunking preserves content and bounds size** ??tagged `Feature: mablop-mvp, Property 1`, ??100 iterations
    - **Validates: Requirements 1.4**

  - [x] 6.3 Implement the Bedrock embedding client (cheap multilingual)
    - Per-chunk embedding calls to the cheap multilingual Bedrock model; Bedrock mocked via botocore Stubber in tests
    - _Requirements: 1.4_
    - _Design: Bedrock model tiering (chunk/input embedding)_

  - [x] 6.4 Implement numpy index build and .npy/.json storage
    - Assemble a `float32` `[N, D]` matrix; write `embeddings/index.npy`, `embeddings/index-map.json`, and `vector-index/manifest.json`; reload with `np.load`
    - _Requirements: 1.5_
    - _Design: Embedding storage format; Data Model_

  - [x]* 6.5 Write property test for embedding index round-trip
    - **Property 2: Embedding index round-trips through storage** ??tagged `Feature: mablop-mvp, Property 2`, ??100 iterations
    - **Validates: Requirements 1.5, 4.4**

  - [x] 6.6 Implement Style_Profile generation via the cheap model
    - Produce the Style_Profile JSON (tone, frequent expressions, paragraph structure, sentence length) via a cheap Bedrock model; store under `style-profile/`
    - _Requirements: 1.6_
    - _Design: Style_Learner step 6; Style_Profile schema_

  - [x] 6.7 Implement S3 checkpoint/resume for the 15-minute timeout
    - Checkpoint intermediate chunks/embeddings to `metadata/learn-checkpoint-<run>.json`; resume from checkpoint without recomputation; re-learn overwrites at the same keys
    - _Requirements: 1.9, 1.10_
    - _Design: Style_Learner step 7-8; Error Handling (timeout/partial data)_

  - [x]* 6.8 Write property test for checkpoint resume equivalence
    - **Property 4: Checkpoint resume equals uninterrupted run** ??tagged `Feature: mablop-mvp, Property 4`, ??100 iterations
    - **Validates: Requirements 1.9**

  - [x]* 6.9 Write property test for re-learning overwrite
    - **Property 5: Re-learning overwrites rather than accumulates** ??tagged `Feature: mablop-mvp, Property 5`, ??100 iterations
    - **Validates: Requirements 1.10**

  - [x] 6.10 Wire the `learn-style` action end-to-end
    - Connect resolve ??crawl/paste ??chunk ??embed ??index/profile store ??checkpoint into the handler's `learn-style` dispatch; return the documented response (status, counts, keys, checkpoint)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10_
    - _Design: Interfaces (learn-style contract); Style_Learner_

- [x] 7. Checkpoint ??style learning works end-to-end
  - Ensure the full learn-style pipeline and its PBTs pass; ask the user if questions arise.

- [x] 8. Phase 7 ??RAG retrieval and prompt construction (Drafting_Engine part 1)
  - [x] 8.1 Implement numpy cosine Top_K search
    - Load `embeddings/index.npy` into numpy; compute cosine similarity vs. the query; return `min(k, N)` indices in score-descending order
    - _Requirements: 2.2, 2.3, 2.4, 7.2_
    - _Design: Data Model (cosine_top_k)_

  - [x]* 8.2 Write property test for cosine Top_K search
    - **Property 6: Cosine Top_K ranking matches a brute-force reference and is scale-invariant** ??tagged `Feature: mablop-mvp, Property 6`, ??100 iterations
    - **Validates: Requirements 2.3, 2.4, 7.2**

  - [x] 8.3 Implement the prompt builder with token cap and untrusted-content partitioning
    - Load Style_Profile; assemble few-shot examples from retrieved chunks under the context token cap; fence crawled text, notes, and captions in delimited DATA sections with delimiter neutralization; templates from `prompt-templates/`
    - _Requirements: 2.5, 2.6, 6.5, 7.1_
    - _Design: Drafting_Engine steps 4-5; Prompt-injection partitioning (Req 6.5)_

  - [x]* 8.4 Write property test for prompt token cap
    - **Property 7: Prompt stays within the context token cap** ??tagged `Feature: mablop-mvp, Property 7`, ??100 iterations
    - **Validates: Requirements 2.6, 7.1**

  - [x]* 8.5 Write property test for untrusted-content partitioning
    - **Property 8: Untrusted content is partitioned from instructions** ??tagged `Feature: mablop-mvp, Property 8`, ??100 iterations; include inputs that mimic instructions/delimiters
    - **Validates: Requirements 6.5**

- [x] 9. Phase 8 ??Post generation and image analysis (Image_Analyzer + Drafting_Engine part 2)
  - [x] 9.1 Implement presigned URL issuance (scoped and constrained)
    - Generate presigned PUT URLs scoped to the `images/` prefix with short expiry and content-type + max-size conditions
    - _Requirements: 3.1, 3.2_
    - _Design: Image_Analyzer (presigned issuance); Presigned URL constraints (Req 3.2)_

  - [ ]* 9.2 Write property test for presigned URL scoping
    - **Property 12: Presigned URLs are scoped and constrained** ??tagged `Feature: mablop-mvp, Property 12`, ??100 iterations
    - **Validates: Requirements 3.2**

  - [x] 9.3 Implement vision captioning with user-description skip and length bound
    - Call the vision model once per image lacking a description; use supplied description verbatim and skip the call where present; bound/truncate caption length; store `images/<imgId>.json`
    - Wire the `analyze-images` action in the handler
    - _Requirements: 3.3, 3.4, 3.5, 4.3, 7.5_
    - _Design: Image_Analyzer (captioning); Bedrock model tiering (vision)_

  - [ ]* 9.4 Write property test for caption resolution counts
    - **Property 10: Caption resolution calls the vision model only when needed** ??tagged `Feature: mablop-mvp, Property 10`, ??100 iterations
    - **Validates: Requirements 3.3, 3.4**

  - [ ]* 9.5 Write property test for caption length bound
    - **Property 11: Captions are bounded in length** ??tagged `Feature: mablop-mvp, Property 11`, ??100 iterations
    - **Validates: Requirements 7.5**

  - [x] 9.6 Implement draft generation with quality text model and image placeholders
    - Invoke the quality Korean Bedrock text model; place `![img-N]` placeholders from captions; validate each supplied index appears exactly once with no out-of-range placeholder and no more than 10 images; store Markdown under `generated-posts/`
    - _Requirements: 2.7, 2.8, 2.9, 3.6, 3.7, 4.1, 4.2_
    - _Design: Drafting_Engine steps 6-8; Bedrock model tiering (draft text-gen)_

  - [x]* 9.7 Write property test for image placeholder well-formedness
    - **Property 9: Image placeholders are well-formed and bounded** ??tagged `Feature: mablop-mvp, Property 9`, ??100 iterations
    - **Validates: Requirements 3.6, 3.7, 4.1, 7.4**

  - [x] 9.8 Wire the `generate-draft` and `get-result` actions end-to-end
    - Connect embed input ??cosine Top_K ??load profile ??prompt build ??generate ??placeholder-validate ??store ??synchronous return in the `generate-draft` dispatch; implement `get-result` status/result retrieval
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 3.6, 3.7, 4.1, 4.2_
    - _Design: Drafting Flow (sequence); Interfaces (generate-draft, get-result)_

- [x] 10. Checkpoint ??backend generation path works end-to-end
  - Ensure drafting + image PBTs and action wiring pass; ask the user if questions arise.

- [x] 11. Phase 3 ??Frontend (sequenced after backend contracts)
  - [x] 11.1 Implement the style-learning view with paste fallback UI
    - Inputs for Naver blog URL + `maxPosts`; per-post paste-fallback text areas revealed on reported crawl failure; call `learn-style`; show run status and resume on `in-progress`
    - _Requirements: 1.1, 1.3_
    - _Design: Frontend view structure (Style-Learning View); Interfaces (learn-style)_

  - [x] 11.2 Implement client-side image resize/compress and the 10-image cap
    - Resize so the largest dimension and byte budget are bounded; enforce the max-10 cap in the attach widget
    - _Requirements: 7.3, 7.4_
    - _Design: Frontend client responsibilities (cost)_

  - [x]* 11.3 Write component test for resize/compress bounds
    - **Property 17: Image resize/compression bounds output** ??tagged `Feature: mablop-mvp, Property 17`, ??100 iterations
    - **Validates: Requirements 7.3**

  - [x] 11.4 Implement the drafting view with presigned PUT upload sending keys only
    - Title/outline/notes fields; image attach with optional per-image description; request presigned URLs, PUT bytes to S3, call `analyze-images`; send only S3 keys to the backend (no binary in the Lambda body)
    - _Requirements: 3.1, 6.1_
    - _Design: Frontend view structure (Drafting View); Design Principle 2_

  - [x]* 11.5 Write component tests for the drafting view upload contract
    - Assert the `generate-draft`/`analyze-images` payload carries keys only; assert the 10-image cap is enforced in the UI
    - _Requirements: 3.1, 7.4_

  - [x] 11.6 Implement the result/Markdown view with synchronous loading state
    - Single synchronous loading state during `generate-draft`; render returned Markdown with a copy/export control
    - _Requirements: 2.9_
    - _Design: Frontend view structure (Result / Markdown View)_

  - [x]* 11.7 Write a static check asserting no Bedrock SDK import
    - Fail the frontend test suite if any Bedrock SDK import appears in `frontend/src`
    - _Requirements: 6.1, 6.2_
    - _Design: Design Principle 1; Frontend client responsibilities_

- [x] 12. Checkpoint ??frontend wired to backend contracts
  - Ensure frontend component tests pass and views call the documented actions; ask the user if questions arise.

- [x] 13. Phase 9 ??Testing consolidation
  - [x]* 13.1 Verify full property-based test coverage of all 17 properties
    - Confirm each of Properties 1-17 has a passing PBT (??100 iterations, correctly tagged); add any missing property test
    - Confirm Bedrock is mocked via botocore Stubber and S3 via moto in all backend tests
    - _Requirements: 1.4, 1.5, 1.8, 1.9, 1.10, 2.3, 2.4, 2.6, 3.2, 3.3, 3.4, 3.6, 3.7, 4.1, 4.4, 5.1, 5.2, 5.3, 5.4, 6.5, 7.1, 7.2, 7.3, 7.4, 7.5_
    - _Design: Testing Strategy; Correctness Properties 1-17_

  - [x]* 13.2 Verify frontend component test coverage
    - Confirm upload-contract, 10-image cap, resize/compress, and no-Bedrock-import tests all pass
    - _Requirements: 3.1, 6.1, 7.3, 7.4_
    - _Design: Testing Strategy (Frontend component tests)_

- [x] 14. Phase 10 ??Deploy / Infrastructure (IaC) and CI/CD
  - [x] 14.1 Author IaC for the Lambda, Function URL, and private SSE S3 bucket
    - Define the single Python Lambda + public Function URL (us-east-1); private bucket with `BlockPublicAccess` fully on, SSE enabled, and the eight required prefixes
    - _Requirements: 6.4, 8.1, 8.2, 8.4, 8.5_
    - _Design: Architecture (System Context); Bucket posture (Req 6.4)_

  - [x] 14.2 Author the least-privilege IAM role
    - Grant only `bedrock:InvokeModel` on the specific model ARNs, `s3:GetObject`/`s3:PutObject` scoped to the defined prefixes, and logs-only on the Lambda log group; no wildcards
    - _Requirements: 6.3_
    - _Design: Least-privilege IAM (Req 6.3)_

  - [x] 14.3 Author the CloudWatch billing alarm
    - Define a billing alarm at the configured monthly threshold
    - _Requirements: 7.6_
    - _Design: Other cost controls (billing alarm)_

  - [x] 14.4 Author the GitHub Actions OIDC deploy workflow and GitHub Pages hosting
    - Deploy backend + infra via GitHub Actions using OIDC federation (no long-lived keys, no secrets in code); publish the frontend to GitHub Pages
    - _Requirements: 9.1, 9.2, 9.3, 9.4_
    - _Design: Repository Structure (.github/workflows)_

  - [x]* 14.5 Write IaC/config snapshot verification checks (not PBT)
    - Snapshot-assert: IAM policy is least-privilege (no `bedrock:*`, no bucket-wide `s3:*`, no account-wide logs); bucket is private + SSE; region is us-east-1; billing alarm present; required AIDLC docs present; GitHub Actions uses OIDC with no static keys/secrets; **excluded services (API Gateway, DynamoDB, Cognito, Step Functions, Bedrock Agent/KB, OpenSearch, RDS, EC2/ECS/Fargate/Kubernetes) are absent from the config**
    - _Requirements: 6.3, 6.4, 7.6, 8.1, 8.5, 9.1, 9.2, 9.3, 10.1, 10.2_
    - _Design: Testing Strategy (IaC / config checks)_

- [x] 15. Final checkpoint ??Ensure all tests pass
  - Ensure all backend unit + property tests, frontend component tests, and IaC/config snapshot checks pass; ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP; they are all test-related (unit, property-based, component, and IaC/config snapshot) or coverage-verification tasks.
- Property-based test tasks run ??100 iterations and are tagged `Feature: mablop-mvp, Property N`; each references a specific property from the design's Correctness Properties section.
- IaC/config snapshot verification (task 14.5) is explicitly **not** a property-based test ??it validates architectural constraints (Req 6.x, 8.x, 9.x, 10.x) that are not expressible as PBTs.
- Phases are ordered to match the AI-DLC development phases: Phase 2 (bootstrap) ??Phase 4 (backend core) ??Phase 5-8 (backend features) ??Phase 3 (frontend, sequenced after backend contracts are fixed) ??Phase 9 (testing) ??Phase 10 (deploy/infra).
- Each task references specific requirement clauses and design sections for traceability; checkpoints enforce incremental validation.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4"] },
    { "id": 2, "tasks": ["3.1", "3.2"] },
    { "id": 3, "tasks": ["3.3", "3.4"] },
    { "id": 4, "tasks": ["3.5", "3.6", "3.7", "3.8"] },
    { "id": 5, "tasks": ["5.1", "5.3"] },
    { "id": 6, "tasks": ["5.2", "5.4", "5.5", "5.6"] },
    { "id": 7, "tasks": ["5.7", "6.1", "6.3", "6.4", "6.6"] },
    { "id": 8, "tasks": ["6.2", "6.5", "6.7"] },
    { "id": 9, "tasks": ["6.8", "6.9", "6.10"] },
    { "id": 10, "tasks": ["8.1", "8.3"] },
    { "id": 11, "tasks": ["8.2", "8.4", "8.5", "9.1", "9.3"] },
    { "id": 12, "tasks": ["9.2", "9.4", "9.5", "9.6"] },
    { "id": 13, "tasks": ["9.7", "9.8"] },
    { "id": 14, "tasks": ["11.1", "11.2", "11.4", "11.6"] },
    { "id": 15, "tasks": ["11.3", "11.5", "11.7"] },
    { "id": 16, "tasks": ["13.1", "13.2"] },
    { "id": 17, "tasks": ["14.1", "14.2", "14.3", "14.4"] },
    { "id": 18, "tasks": ["14.5"] }
  ]
}
```
