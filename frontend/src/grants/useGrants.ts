// Grant selection for members (Req 21). The hook loads the member's Grants, keeps
// only the usable ones as options, and remembers the choice in localStorage under
// mablop-grant-id:{loginId}. Admins, and a view rendered without a provider
// (user null), get enabled false and no request is sent (Req 21.9).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiRequestError, postJson } from "../api";
import { useAuth } from "../auth/AuthContext";

export interface GrantOption {
  id: string;
  status: string;
  purpose: string | null;
  requestedAt: string;
  decidedAt: string | null;
  expiresAt: string | null;
  expired: boolean;
}

interface GrantListResponse {
  grants: GrantOption[];
}

export interface GrantsState {
  /** true only for a logged-in member. */
  enabled: boolean;
  /** Approved and not expired Grants, in the order the Backend returned them. */
  options: GrantOption[];
  /** The chosen Grant id, or null. Never set automatically (D22). */
  selectedId: string | null;
  select: (id: string | null) => void;
  reload: () => Promise<void>;
  loading: boolean;
  error: string | null;
  /** false while a member has no selected Grant. Admins are never blocked. */
  canCallBedrock: boolean;
  /** Adds grantId to a Bedrock_Action body for a member; admins get the body unchanged. */
  withGrant: (body: Record<string, unknown>) => Record<string, unknown>;
  /** On 403 `forbidden` reloads the options and returns true. The caller shows err.message. */
  handleGrantError: (err: unknown) => boolean;
}

export const GRANT_ID_KEY_PREFIX = "mablop-grant-id:";

const LOAD_ERROR_MESSAGE = "Bedrock 사용 권한을 불러오지 못했습니다.";

function grantKey(loginId: string): string {
  return `${GRANT_ID_KEY_PREFIX}${loginId}`;
}

function readStoredId(loginId: string): string | null {
  try {
    return localStorage.getItem(grantKey(loginId));
  } catch {
    return null;
  }
}

function writeStoredId(loginId: string, id: string): void {
  try {
    localStorage.setItem(grantKey(loginId), id);
  } catch {
    // Storage blocked: the selection lives in memory only.
  }
}

function removeStoredId(loginId: string): void {
  try {
    localStorage.removeItem(grantKey(loginId));
  } catch {
    // Ignore storage errors.
  }
}

function isUsable(grant: GrantOption): boolean {
  return grant.status === "approved" && grant.expired === false;
}

export function useGrants(): GrantsState {
  const { user } = useAuth();
  const enabled = user?.role === "member";
  const loginId = user?.loginId ?? "";

  const [options, setOptions] = useState<GrantOption[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(enabled);
  const [error, setError] = useState<string | null>(null);

  // selectedRef mirrors selectedId so a reload sees the current choice.
  const selectedRef = useRef<string | null>(null);
  // Each load gets a number; only the latest one may write state.
  const requestRef = useRef(0);

  const reload = useCallback(async () => {
    if (!enabled) return;
    requestRef.current += 1;
    const requestId = requestRef.current;
    setLoading(true);
    setError(null);
    try {
      const res = await postJson<GrantListResponse>({ action: "grant-list-mine" });
      if (requestId !== requestRef.current) return;
      const grants = Array.isArray(res?.grants) ? res.grants : [];
      const usable = grants.filter(isUsable);
      // A stored id that is not an option is deleted and nothing is selected (Req 21.5).
      const candidate = selectedRef.current ?? readStoredId(loginId);
      const keep = candidate !== null && usable.some((g) => g.id === candidate);
      if (!keep && candidate !== null) removeStoredId(loginId);
      selectedRef.current = keep ? candidate : null;
      setOptions(usable);
      setSelectedId(selectedRef.current);
    } catch (err) {
      if (requestId !== requestRef.current) return;
      selectedRef.current = null;
      setOptions([]);
      setSelectedId(null);
      setError(err instanceof ApiRequestError ? err.message : LOAD_ERROR_MESSAGE);
    } finally {
      if (requestId === requestRef.current) setLoading(false);
    }
  }, [enabled, loginId]);

  useEffect(() => {
    selectedRef.current = null;
    setSelectedId(null);
    setOptions([]);
    setError(null);
    if (!enabled) {
      setLoading(false);
      return undefined;
    }
    void reload();
    return () => {
      // Ignore a response that arrives after unmount or a user change.
      requestRef.current += 1;
    };
  }, [enabled, loginId, reload]);

  const select = useCallback(
    (id: string | null) => {
      if (!enabled) return;
      if (id !== null && id !== "" && options.some((g) => g.id === id)) {
        writeStoredId(loginId, id);
        selectedRef.current = id;
        setSelectedId(id);
        return;
      }
      removeStoredId(loginId);
      selectedRef.current = null;
      setSelectedId(null);
    },
    [enabled, loginId, options],
  );

  const withGrant = useCallback(
    (body: Record<string, unknown>): Record<string, unknown> =>
      enabled ? { ...body, grantId: selectedId } : body,
    [enabled, selectedId],
  );

  const handleGrantError = useCallback(
    (err: unknown): boolean => {
      if (err instanceof ApiRequestError && err.status === 403 && err.code === "forbidden") {
        void reload();
        return true;
      }
      return false;
    },
    [reload],
  );

  const canCallBedrock = !enabled || selectedId !== null;

  return useMemo(
    () => ({
      enabled,
      options,
      selectedId,
      select,
      reload,
      loading,
      error,
      canCallBedrock,
      withGrant,
      handleGrantError,
    }),
    [enabled, options, selectedId, select, reload, loading, error, canCallBedrock, withGrant, handleGrantError],
  );
}
