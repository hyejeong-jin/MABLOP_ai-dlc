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
