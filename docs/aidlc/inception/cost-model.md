# Cost Model (summary)

> Source: `.kiro/specs/mablop-mvp/design.md` (Cost Design). Target infra USD 5-20/month; Bedrock billed separately = main variable cost. Estimated infra < $1/month.

## Bedrock model tiering (primary lever)

| Purpose | Tier | Rationale |
|---|---|---|
| Chunk/input embedding | cheap multilingual | high call volume, quality less critical |
| Draft text generation | quality Korean | user-facing output quality |
| Style profile / proofing | cheap | short, infrequent, structured |
| Image captioning | low/mid multimodal, once per image | short captions; skip when user supplies one |

## Other cost controls

- Context token cap on prompts (R7.1).
- Top_K limit on retrieval (R7.2).
- Image resize/compress before upload + 10-image cap (R7.3, R7.4).
- Short captions (R7.5).
- CloudWatch billing alarm at threshold (R7.6).

## Pending

- Exact Bedrock model IDs + us-east-1 per-token prices to be locked in `decisions.md` before Phase 8. Those values drive the real variable-cost estimate.
