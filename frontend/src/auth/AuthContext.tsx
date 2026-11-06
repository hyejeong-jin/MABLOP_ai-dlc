// Login session state for the whole app (Req 10.3, 13.4-13.8, 14.6, 23.11).
// AuthProvider owns the session user and the expiry notice. Views read them
// through useAuth(). Without a provider, useAuth() returns user null and no-op
// functions so a view can be rendered alone in tests.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { SESSION_EXPIRED_MESSAGE, postJson, setUnauthorizedHandler } from "../api";
import {
  clearSession,
  purgeLegacyStorage,
  readSession,
  writeSession,
  type SessionUser,
} from "./session";

interface LoginResponse {
  token: string;
  expiresAt: string;
  user: SessionUser;
}

interface RegisterResponse {
  message: string;
}

export interface AuthContextValue {
  user: SessionUser | null;
  notice: string | null;
  login: (loginId: string, password: string) => Promise<void>;
  // Returns the backend message. It does not log in (Req 13.4).
  register: (loginId: string, name: string, password: string) => Promise<string>;
  logout: () => void;
  // Rewrites only token and expiresAt of the stored session (Req 14.6).
  replaceToken: (token: string, expiresAt: string) => void;
}

const NO_PROVIDER_VALUE: AuthContextValue = {
  user: null,
  notice: null,
  login: async () => {},
  register: async () => "",
  logout: () => {},
  replaceToken: () => {},
};

const AuthContext = createContext<AuthContextValue>(NO_PROVIDER_VALUE);

export function AuthProvider({ children }: { children: ReactNode }) {
  // Runs once at startup: drop legacy entries, then restore a still-valid session.
  // readSession removes an expired or malformed entry (Req 13.6).
  const [user, setUser] = useState<SessionUser | null>(() => {
    purgeLegacyStorage();
    return readSession()?.user ?? null;
  });
  const [notice, setNotice] = useState<string | null>(null);

  // A 401 on an authenticated request ends the session (Req 13.7).
  useEffect(() => {
    setUnauthorizedHandler(() => {
      clearSession();
      setUser(null);
      setNotice(SESSION_EXPIRED_MESSAGE);
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const login = useCallback(async (loginId: string, password: string) => {
    const res = await postJson<LoginResponse>({ action: "login", loginId, password });
    writeSession({
      token: res.token,
      expiresAt: res.expiresAt,
      user: {
        loginId: res.user.loginId,
        name: res.user.name,
        role: res.user.role,
      },
    });
    setUser({
      loginId: res.user.loginId,
      name: res.user.name,
      role: res.user.role,
    });
    setNotice(null);
  }, []);

  const register = useCallback(
    async (loginId: string, name: string, password: string) => {
      const res = await postJson<RegisterResponse>({
        action: "register",
        loginId,
        name,
        password,
      });
      return res.message;
    },
    [],
  );

  const logout = useCallback(() => {
    clearSession();
    setUser(null);
    setNotice(null);
  }, []);

  const replaceToken = useCallback((token: string, expiresAt: string) => {
    const current = readSession();
    if (!current) return;
    writeSession({ ...current, token, expiresAt });
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ user, notice, login, register, logout, replaceToken }),
    [user, notice, login, register, logout, replaceToken],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  return useContext(AuthContext);
}
