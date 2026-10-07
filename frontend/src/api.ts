// Central API client. Browser -> Lambda Function URL only. NO Bedrock SDK (Req 6.1).
// Base URL from env; token sent via X-Mablop-Token header (Req 5.1).

const BASE_URL = import.meta.env.VITE_MABLOP_API_URL ?? "";
const TOKEN_HEADER = "X-Mablop-Token";

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

// POST JSON with the token header. Later tasks (11.x) build real action payloads on this.
export async function postJson<TResponse>(
  body: Record<string, unknown>,
  token: string,
): Promise<TResponse> {
  const res = await fetch(BASE_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      [TOKEN_HEADER]: token,
    },
    body: JSON.stringify(body),
  });

  const data = (await res.json().catch(() => null)) as
    | { error?: ApiError }
    | TResponse
    | null;

  if (!res.ok) {
    const err = (data as { error?: ApiError } | null)?.error;
    throw new ApiRequestError(
      res.status,
      err?.code ?? "unknown",
      err?.message ?? `request failed (${res.status})`,
    );
  }

  return data as TResponse;
}
