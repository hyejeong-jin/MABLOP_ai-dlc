// Central API client. Browser -> Lambda Function URL only. NO Bedrock SDK (Req 6.1).
// Base URL from env; the Session_Token is read from the stored login session and
// sent via the X-Mablop-Token header (Req 13.9).

import { clearSession, readSession } from "./auth/session";

const BASE_URL = import.meta.env.VITE_MABLOP_API_URL ?? "";
const TOKEN_HEADER = "X-Mablop-Token";

// Actions that are sent without a token. Mirrors the backend Auth_Exempt_Actions.
export const PUBLIC_ACTIONS: ReadonlySet<string> = new Set([
  "login",
  "register",
  "bootstrap-admin",
]);

export const SESSION_EXPIRED_MESSAGE = "로그인이 만료되었습니다. 다시 로그인하세요.";
export const NETWORK_ERROR_MESSAGE =
  "서버에 연결할 수 없습니다. 잠시 후 다시 시도하세요.";

// Common error shape from the backend: {"error": {"code","message"}}.
export interface ApiError {
  code: string;
  message: string;
}

export class ApiRequestError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.code = code;
  }
}

// AuthProvider registers a callback that runs when an authenticated request gets 401.
let unauthorizedHandler: (() => void) | null = null;

export function setUnauthorizedHandler(fn: (() => void) | null): void {
  unauthorizedHandler = fn;
}

function isPublicAction(body: Record<string, unknown>): boolean {
  return typeof body.action === "string" && PUBLIC_ACTIONS.has(body.action);
}

function fallbackMessage(status: number): string {
  return `요청을 처리하지 못했습니다. (HTTP ${status})`;
}

// POST JSON. The token comes from the stored session and is attached to every
// action except the public ones. The optional second parameter is the old token
// argument; it is ignored so call sites that still pass it keep compiling until
// they are migrated.
export async function postJson<TResponse>(
  body: Record<string, unknown>,
  _legacyToken?: string,
): Promise<TResponse> {
  const isPublic = isPublicAction(body);
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (!isPublic) {
    const session = readSession();
    if (session) headers[TOKEN_HEADER] = session.token;
  }

  let res: Response;
  try {
    res = await fetch(BASE_URL, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiRequestError(0, "network", NETWORK_ERROR_MESSAGE);
  }

  const data = (await res.json().catch(() => null)) as
    | { error?: ApiError }
    | TResponse
    | null;

  if (!res.ok) {
    if (res.status === 401 && !isPublic) {
      clearSession();
      try {
        unauthorizedHandler?.();
      } catch {
        // A failing callback must not hide the 401.
      }
      const err = (data as { error?: ApiError } | null)?.error;
      throw new ApiRequestError(401, err?.code ?? "unauthorized", SESSION_EXPIRED_MESSAGE);
    }
    const err = (data as { error?: ApiError } | null)?.error;
    throw new ApiRequestError(
      res.status,
      err?.code ?? "unknown",
      err?.message ?? fallbackMessage(res.status),
    );
  }

  return data as TResponse;
}

// Shared action names and response contracts for user-owned feature APIs.
// The backend derives ownership from the verified Session_Token userId; any
// identity-like fields sent by a caller are ignored for authorization and keys.
export type VocAction =
  | "create-voc"
  | "list-voc"
  | "get-voc"
  | "update-voc"
  | "archive-voc"
  | "admin-list-voc"
  | "admin-update-voc-status";
export type ResearchAction = "run-research" | "get-research-run" | "list-research-runs";
export type ActivityAction = "get-activity";
export type FeatureAction = VocAction | ResearchAction | ActivityAction;

export interface FeatureActionRequest {
  action: FeatureAction;
  [field: string]: unknown;
}

export type SourceLabel = "fact" | "generated" | "user-input" | "unverified";
export type VocStatus = "new" | "reviewing" | "confirmed" | "archived";

export interface VocResponse {
  voc: Record<string, unknown>;
}

export interface VocListResponse {
  records: Array<Record<string, unknown>>;
  nextCursor?: string;
}

export interface ResearchResponse {
  runId: string;
  status: string;
  summary?: Record<string, unknown>;
  sources: Array<Record<string, unknown>>;
  usage: Record<string, unknown>;
  budget: Record<string, unknown>;
  cacheHit: boolean;
  controls: string[];
}

export interface ResearchRunResponse {
  run: Record<string, unknown>;
  summary: Record<string, unknown>;
  sources: Array<Record<string, unknown>>;
}

export interface ResearchSource {
  sourceId: string;
  sourceType?: string;
  title?: string | null;
  domain?: string | null;
  url?: string | null;
  normalizedUrl?: string | null;
  status?: string;
}

export interface ResearchSummaryItem {
  text: string;
  sourceIds?: string[];
  sourceLabel?: SourceLabel;
}

export interface ResearchSummaryData {
  facts?: ResearchSummaryItem[];
  generatedInterpretation?: ResearchSummaryItem[];
  unverified?: ResearchSummaryItem[];
  userInput?: ResearchSummaryItem[];
  partial?: boolean;
  omitted?: string | null;
}

export interface ResearchListResponse {
  runs: Array<Record<string, unknown>>;
  nextCursor?: string;
}

export interface ActivityResponse {
  events: Array<Record<string, unknown>>;
  daily: Array<Record<string, unknown>>;
  range: { from: string; to: string };
}

// --- VOC (Voice Box) types and helpers ---------------------------------------
export type VocSourceType = "customer" | "observation" | "quote" | "request" | "other";

export const VOC_SOURCE_TYPES: ReadonlyArray<{ value: VocSourceType; label: string }> = [
  { value: "customer", label: "고객" },
  { value: "observation", label: "관찰" },
  { value: "quote", label: "인용" },
  { value: "request", label: "요청" },
  { value: "other", label: "기타" },
];

export const VOC_STATUS_LABELS: Record<VocStatus, string> = {
  new: "새 의견",
  reviewing: "검토 중",
  confirmed: "확인됨",
  archived: "보관됨",
};

export const VOC_LIMITS = { body: 20000, note: 5000, tags: 20, tagLength: 100 } as const;

// List entry returned by list-voc (index fields only).
export interface VocSummary {
  vocId: string;
  preview: string;
  status: VocStatus;
  createdAt: string;
  updatedAt: string;
}

// Full record returned by create/get/update/archive.
export interface VocRecord {
  vocId: string;
  body: string;
  userMetadata: { sourceType: VocSourceType; tags: string[]; note: string };
  status: VocStatus;
  createdAt: string;
  updatedAt: string;
}

export interface VocInput {
  body: string;
  sourceType: VocSourceType;
  tags: string[];
  note: string;
}

export const createVoc = (input: VocInput) =>
  postJson<{ voc: VocRecord }>({ action: "create-voc", ...input });
export const listVoc = (params: { status?: VocStatus; limit?: number; cursor?: string } = {}) =>
  postJson<{ records: VocSummary[]; nextCursor?: string | null }>({ action: "list-voc", ...params });
export const getVoc = (vocId: string) => postJson<{ voc: VocRecord }>({ action: "get-voc", vocId });
export const updateVoc = (vocId: string, input: VocInput) =>
  postJson<{ voc: VocRecord }>({ action: "update-voc", vocId, ...input });
export const archiveVoc = (vocId: string) =>
  postJson<{ voc: VocRecord }>({ action: "archive-voc", vocId });

// --- Research (글 재료 수집) types and helpers ---------------------------------
export type ResearchStatus = "completed" | "partial" | "budget-exhausted" | "failed";

export interface ResearchUsage {
  requests?: number;
  inputChars?: number;
  outputTokens?: number;
  searchCalls?: number;
  cacheHits?: number;
  cacheMisses?: number;
}

export interface ResearchBudget {
  maxRequests?: number | null;
  maxInputChars?: number | null;
  maxOutputTokens?: number | null;
  maxSearchCalls?: number | null;
}

export interface ResearchRunSource extends ResearchSource {
  errorMessage?: string | null;
  errorCode?: string | null;
}

export interface ResearchRunRequest {
  links: string[];
  search?: { enabled: true; queries: string[] };
  budget?: ResearchBudget;
}

export interface ResearchRunResult {
  runId: string;
  status: ResearchStatus | string;
  summary?: ResearchSummaryData;
  sources: ResearchRunSource[];
  usage: ResearchUsage;
  budget: ResearchBudget;
  cacheHit: boolean;
  controls: string[];
}

export interface ResearchRunSummary {
  runId: string;
  status: ResearchStatus | string;
  requestedAt?: string;
  usage?: ResearchUsage;
}

export const runResearch = (input: ResearchRunRequest & { grantId?: string }) =>
  postJson<ResearchRunResult>({ action: "run-research", ...input });
export const getResearchRun = (runId: string) =>
  postJson<{ run: ResearchRunSummary; summary: ResearchSummaryData; sources: ResearchRunSource[] }>({
    action: "get-research-run",
    runId,
  });
export const listResearchRuns = (params: { limit?: number; cursor?: string } = {}) =>
  postJson<{ runs: ResearchRunSummary[]; nextCursor?: string | null }>({
    action: "list-research-runs",
    ...params,
  });

// --- Draft provenance (returned by generate-draft / get-result when research was used) ---
export interface DraftProvenanceSource {
  sourceId: string;
  title?: string;
  domain?: string;
  url?: string;
  sourceLabel?: SourceLabel;
}

export interface DraftProvenanceSegment {
  label: SourceLabel;
  text: string;
  sourceIds?: string[];
}

export interface DraftProvenance {
  draftId?: string;
  researchRunId?: string | null;
  researchStatus?: string;
  sources?: DraftProvenanceSource[];
  vocIds?: string[];
  segments?: DraftProvenanceSegment[];
  generalStyle?: boolean;
  createdAt?: string;
}

// --- Draft tier (Req 15.1, 15.2) ---------------------------------------------
// The server decides the tier: a verified grantId gives "quality", no grantId gives "lite".
export type DraftTier = "quality" | "lite";

// generate-draft response. tier is always present; liteRemaining only for lite drafts.
export interface DraftResponse {
  draftId: string;
  markdown: string;
  draftKey?: string;
  topK?: number;
  generalStyle?: boolean;
  provenance?: DraftProvenance;
  tier?: DraftTier;
  liteRemaining?: number;
}

// get-usage response (only the fields the drafting view reads).
export interface GetUsageResponse {
  liteDrafts?: { used: number; limit: number };
}

export const IGNORED_IDENTITY_FIELDS = ["userId", "loginId", "s3Key", "key", "userPrefix"] as const;
