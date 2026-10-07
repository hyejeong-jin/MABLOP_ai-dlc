# Requirements Document

## Introduction

MABLOP (My Blog Posting Agent) is a personal, very-small-scale AI blog-drafting SaaS intended for 2-3 trusted users with intermittent usage. The system learns a user's Korean writing style from their Naver blog and generates style-matched post drafts using retrieval-augmented generation (RAG). The top-priority non-functional requirement is minimizing operating cost, targeting infrastructure spend of USD 5-20 per month, with Amazon Bedrock billed separately as the main variable cost.

The system follows YAGNI principles and uses the minimum set of AWS services: S3 for all storage (no database), numpy-based cosine similarity for vector search (no FAISS/OpenSearch/Pinecone/Weaviate), a public Lambda Function URL for the backend, a React frontend on GitHub Pages, and Amazon Bedrock for text generation and vision analysis. The deployment region is us-east-1. The following AWS services are explicitly excluded: API Gateway, DynamoDB, Cognito, Step Functions, Bedrock Agent/Knowledge Base, OpenSearch, RDS, EC2/ECS/Fargate/Kubernetes, and headless browser crawling.

This document defines the requirements for the MVP, covering blog style learning, AI blog drafting, image context-aware placement, output export, and the supporting non-functional constraints for access control, security, cost, infrastructure, CI/CD, and documentation deliverables.

## Glossary

- **MABLOP_System**: The complete MABLOP application, including the React frontend, the Python Lambda backend, S3 storage, and Bedrock integrations.
- **Frontend**: The React (TypeScript) single-page application hosted on GitHub Pages that users interact with directly.
- **Backend**: The Python AWS Lambda function exposed via a public Function URL that performs crawling, embedding, retrieval, prompt construction, and Bedrock invocation.
- **Style_Learner**: The Backend component responsible for FR-1 blog style learning.
- **Drafting_Engine**: The Backend component responsible for FR-2 AI blog drafting.
- **Image_Analyzer**: The Backend component responsible for FR-3 image caption generation and placement.
- **Access_Controller**: The Backend component that validates the pre-shared token and enforces request rate and length limits.
- **Style_Profile**: A JSON artifact describing a user's writing style, derived from crawled or pasted post bodies, stored under the `style-profile/` S3 prefix.
- **Vector_Index**: A numpy-based array of embeddings over past post chunks, stored under the `vector-index/` and `embeddings/` S3 prefixes.
- **Post_Chunk**: A segment of a past blog post body used as a unit for embedding and retrieval.
- **Draft**: A generated blog post in Markdown format containing image placeholders, stored under the `generated-posts/` S3 prefix.
- **Image_Caption**: A short topic/description string produced by the vision model or supplied by the user, stored as metadata under the `images/` prefix.
- **Pre_Shared_Token**: A single secret string that must accompany every Backend request for authorization.
- **MAX_POST_COUNT**: The configured maximum number of past posts processed during a single style-learning run.
- **Top_K**: The configured maximum number of most-similar Post_Chunks retrieved during drafting.
- **Presigned_URL**: A time-limited, scoped S3 URL that allows the browser to upload an image directly to S3.
- **Bedrock**: Amazon Bedrock, the managed service used for text generation and vision analysis.
- **AIDLC_Docs**: The AI-DLC documentation deliverables stored under `docs/aidlc/`.

## Requirements

### Requirement 1: Blog Style Learning

**User Story:** As a MABLOP user, I want the system to learn my writing style from my Naver blog, so that generated drafts match how I naturally write.

#### Acceptance Criteria

1. WHEN a user submits a Naver blog URL for style learning, THE Style_Learner SHALL crawl the blog, preferring the `m.blog.naver.com` mobile URL or the real iframe body URL.
2. WHEN crawled post pages are retrieved, THE Style_Learner SHALL extract the post body text from each page.
3. IF automatic crawling of a post fails, THEN THE Style_Learner SHALL accept manually pasted raw post text as a fallback input for that post.
4. WHEN post bodies are available, THE Style_Learner SHALL split each body into Post_Chunks and generate embeddings for each Post_Chunk.
5. WHEN embeddings are generated, THE Style_Learner SHALL build a numpy Vector_Index and store the Vector_Index under the `vector-index/` and `embeddings/` S3 prefixes.
6. WHEN style learning completes, THE Style_Learner SHALL produce a Style_Profile and store the Style_Profile under the `style-profile/` S3 prefix.
7. WHEN raw post bodies are extracted, THE Style_Learner SHALL store the raw bodies under the `raw-posts/` S3 prefix.
8. WHILE processing posts during a single style-learning run, THE Style_Learner SHALL process no more than MAX_POST_COUNT posts.
9. WHILE a style-learning run is executing, THE Style_Learner SHALL chunk the work and checkpoint intermediate results to S3 so that a single Lambda invocation completes within the 15-minute Lambda timeout.
10. WHERE a user requests re-learning, THE Style_Learner SHALL re-run style learning as a manual operation and overwrite the stored Style_Profile and Vector_Index.

### Requirement 2: AI Blog Drafting

**User Story:** As a MABLOP user, I want to generate a style-matched blog draft from a title, outline, and notes, so that I can produce posts quickly in my own voice.

#### Acceptance Criteria

1. WHEN a user submits a title, outline, and notes for drafting, THE Drafting_Engine SHALL accept the inputs together with up to 10 images.
2. WHEN drafting inputs are received, THE Drafting_Engine SHALL generate an embedding of the input content.
3. WHEN the input embedding is available, THE Drafting_Engine SHALL perform a Top_K cosine similarity search over past Post_Chunks using numpy over the S3-stored embeddings.
4. WHILE performing retrieval, THE Drafting_Engine SHALL retrieve no more than Top_K Post_Chunks.
5. WHEN retrieval completes, THE Drafting_Engine SHALL load the stored Style_Profile for prompt construction.
6. WHEN constructing the Bedrock prompt, THE Drafting_Engine SHALL include few-shot examples from retrieved Post_Chunks and SHALL limit the prompt to the configured context token cap.
7. WHEN the prompt is constructed, THE Drafting_Engine SHALL invoke Bedrock text generation to produce a Draft.
8. WHEN a Draft is produced, THE Drafting_Engine SHALL store the Draft under the `generated-posts/` S3 prefix.
9. WHILE a drafting request is being processed, THE Drafting_Engine SHALL return the Draft synchronously within the Lambda timeout so the Frontend can display it in a single waiting interaction.

### Requirement 3: Image Context-Aware Placement

**User Story:** As a MABLOP user, I want my uploaded images placed in contextually appropriate positions in the draft, so that the post reads naturally without manual rearranging.

#### Acceptance Criteria

1. WHEN a user adds images for a draft, THE Frontend SHALL upload up to 10 images directly to S3 using a Presigned_URL and SHALL send only the resulting S3 keys to the Backend.
2. THE MABLOP_System SHALL issue each Presigned_URL with a short expiry, a scoped S3 prefix under `images/`, and content-type and size limits.
3. IF a user does not supply a description for an image, THEN THE Image_Analyzer SHALL invoke the default vision model once for that image to produce a short Image_Caption.
4. WHERE a user supplies their own description for an image, THE Image_Analyzer SHALL use the supplied description as the Image_Caption and SHALL skip the vision model call for that image.
5. WHEN an Image_Caption is produced or supplied, THE Image_Analyzer SHALL store the Image_Caption as metadata under the `images/` S3 prefix for reuse.
6. WHEN generating a Draft, THE Drafting_Engine SHALL place `![img-N]` style Markdown placeholders at contextually appropriate positions using the Image_Captions.
7. WHILE a draft includes images, THE Drafting_Engine SHALL reference no more than 10 images.

### Requirement 4: Draft Output and Storage

**User Story:** As a MABLOP user, I want the finished draft exported as Markdown with image placeholders, so that I can review and publish it on my blog.

#### Acceptance Criteria

1. WHEN a Draft is finalized, THE Drafting_Engine SHALL export the Draft as Markdown containing `![img-N]` image placeholders.
2. WHEN a Draft is stored, THE Drafting_Engine SHALL write the Draft to the `generated-posts/` S3 prefix.
3. WHEN images are stored, THE MABLOP_System SHALL store image files under the `images/` S3 prefix with associated Image_Caption metadata.
4. THE MABLOP_System SHALL store all metadata as JSON files under the appropriate S3 prefixes.

### Requirement 5: Access Control

**User Story:** As the system owner, I want all backend requests gated by a shared token with rate and length limits, so that the public endpoint is not abused by unauthorized callers.

#### Acceptance Criteria

1. IF a request to the public Function URL does not include a valid Pre_Shared_Token, THEN THE Access_Controller SHALL reject the request.
2. WHEN a request includes a valid Pre_Shared_Token, THE Access_Controller SHALL allow the request to proceed to Backend processing.
3. WHEN requests are received, THE Access_Controller SHALL enforce a request rate limit and reject requests that exceed the configured rate.
4. WHEN a request body exceeds the configured length limit, THE Access_Controller SHALL reject the request.

### Requirement 6: Security

**User Story:** As the system owner, I want strict security boundaries and least-privilege access, so that credentials, user data, and the Bedrock integration are protected.

#### Acceptance Criteria

1. THE Frontend SHALL send all generation requests to the Backend and SHALL NOT call Bedrock directly.
2. THE Backend SHALL be the only component that invokes Bedrock, following the browser-to-Lambda-to-Bedrock path.
3. THE MABLOP_System SHALL grant the Backend IAM permissions limited to `bedrock:InvokeModel` on specific model ARNs, read and write access scoped to the defined S3 prefixes, and log write permissions only.
4. THE MABLOP_System SHALL configure all S3 buckets as private with server-side encryption enabled.
5. WHEN the Backend incorporates crawled content, user notes, or Image_Captions into a prompt, THE Backend SHALL treat that content as untrusted and SHALL partition it from system instructions to mitigate prompt injection.

### Requirement 7: Cost Control

**User Story:** As the system owner, I want cost controls built into the system, so that monthly spend stays within the USD 5-20 infrastructure target.

#### Acceptance Criteria

1. WHEN constructing prompts, THE Drafting_Engine SHALL enforce a context token cap.
2. WHEN performing retrieval, THE Drafting_Engine SHALL enforce the Top_K limit.
3. WHEN handling uploaded images, THE MABLOP_System SHALL resize and compress each image.
4. WHEN handling images for a draft, THE MABLOP_System SHALL enforce a maximum of 10 images per draft.
5. WHEN generating an Image_Caption, THE Image_Analyzer SHALL produce a short caption.
6. WHEN monthly spend reaches the configured threshold, THE MABLOP_System SHALL trigger a billing alarm.

### Requirement 8: Infrastructure Constraints

**User Story:** As the system owner, I want the system built on a minimal, specific set of AWS services, so that operating cost and complexity stay low.

#### Acceptance Criteria

1. THE MABLOP_System SHALL deploy in the us-east-1 region.
2. THE MABLOP_System SHALL use S3 as the only storage service and SHALL NOT use a database service.
3. THE MABLOP_System SHALL perform vector search using numpy cosine similarity and SHALL NOT use FAISS, OpenSearch, Pinecone, or Weaviate.
4. THE MABLOP_System SHALL organize S3 storage using the prefixes `raw-posts/`, `embeddings/`, `vector-index/`, `style-profile/`, `images/`, `generated-posts/`, `prompt-templates/`, and `metadata/`.
5. THE MABLOP_System SHALL exclude API Gateway, DynamoDB, Cognito, Step Functions, Bedrock Agent, Bedrock Knowledge Base, OpenSearch, RDS, EC2, ECS, Fargate, Kubernetes, and headless browser crawling from its architecture.

### Requirement 9: CI/CD and Hosting

**User Story:** As the system owner, I want automated deployment with short-lived credentials and no secrets in code, so that releases are secure and repeatable.

#### Acceptance Criteria

1. THE MABLOP_System SHALL deploy using GitHub Actions workflows.
2. WHEN GitHub Actions authenticates to AWS, THE MABLOP_System SHALL use OIDC federation and SHALL NOT use long-lived access keys.
3. THE MABLOP_System SHALL keep all secrets out of source code.
4. THE MABLOP_System SHALL host the Frontend on GitHub Pages.

### Requirement 10: AI-DLC Documentation Deliverables

**User Story:** As the system owner, I want the AI-DLC documentation deliverables maintained, so that project context, architecture, decisions, cost, and security are recorded for ongoing operations.

#### Acceptance Criteria

1. THE MABLOP_System SHALL maintain AIDLC_Docs under the `docs/aidlc/inception/`, `docs/aidlc/construction/`, and `docs/aidlc/operations/` directories.
2. THE MABLOP_System SHALL include the documents `project-context.md`, `requirements.md`, `architecture.md`, `decisions.md`, `cost-model.md`, `security.md`, and `roadmap.md` within the AIDLC_Docs.
3. THE MABLOP_System SHALL maintain `state.md` and `backlog.md` as part of the AIDLC_Docs.
