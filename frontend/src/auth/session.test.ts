import { beforeEach, describe, expect, it } from "vitest";
import {
  SESSION_KEY,
  clearSession,
  purgeLegacyStorage,
  readSession,
  writeSession,
  type Session,
} from "./session";

const NOW = Date.parse("2025-01-01T00:00:00.000Z");

function makeSession(expiresAt: string): Session {
  return {
    token: "tok-123",
    expiresAt,
    user: { loginId: "alice", name: "앨리스", role: "member" },
  };
}

beforeEach(() => {
  localStorage.clear();
});

describe("writeSession / readSession", () => {
  it("returns null when nothing is stored", () => {
    expect(readSession(NOW)).toBeNull();
  });

  it("round-trips a valid unexpired session", () => {
    const session = makeSession("2025-01-01T01:00:00.000Z");
    writeSession(session);
    expect(localStorage.getItem(SESSION_KEY)).not.toBeNull();
    expect(readSession(NOW)).toEqual(session);
  });

  it("stores only the known fields", () => {
    const session = {
      ...makeSession("2025-01-01T01:00:00.000Z"),
      extra: "x",
    } as Session;
    writeSession(session);
    const stored = JSON.parse(localStorage.getItem(SESSION_KEY) as string);
    expect(Object.keys(stored).sort()).toEqual(["expiresAt", "token", "user"]);
    expect(Object.keys(stored.user).sort()).toEqual(["loginId", "name", "role"]);
  });

  it("keeps an admin role", () => {
    const session: Session = {
      ...makeSession("2025-01-01T01:00:00.000Z"),
      user: { loginId: "root", name: "관리자", role: "admin" },
    };
    writeSession(session);
    expect(readSession(NOW)?.user.role).toBe("admin");
  });

  it("clearSession removes the stored session", () => {
    writeSession(makeSession("2025-01-01T01:00:00.000Z"));
    clearSession();
    expect(localStorage.getItem(SESSION_KEY)).toBeNull();
    expect(readSession(NOW)).toBeNull();
  });

  it("clearSession leaves other keys alone", () => {
    localStorage.setItem("mablop-grant-id:alice", "g1");
    writeSession(makeSession("2025-01-01T01:00:00.000Z"));
    clearSession();
    expect(localStorage.getItem("mablop-grant-id:alice")).toBe("g1");
  });
});

describe("readSession expiry", () => {
  it("is valid one millisecond before expiresAt", () => {
    writeSession(makeSession(new Date(NOW + 1).toISOString()));
    expect(readSession(NOW)).not.toBeNull();
    expect(localStorage.getItem(SESSION_KEY)).not.toBeNull();
  });

  it("is expired exactly at expiresAt (<= boundary) and removes the entry", () => {
    writeSession(makeSession(new Date(NOW).toISOString()));
    expect(readSession(NOW)).toBeNull();
    expect(localStorage.getItem(SESSION_KEY)).toBeNull();
  });

  it("is expired after expiresAt and removes the entry", () => {
    writeSession(makeSession(new Date(NOW - 1).toISOString()));
    expect(readSession(NOW)).toBeNull();
    expect(localStorage.getItem(SESSION_KEY)).toBeNull();
  });

  it("treats an unparseable expiresAt as expired and removes the entry", () => {
    writeSession(makeSession("not-a-date"));
    expect(readSession(NOW)).toBeNull();
    expect(localStorage.getItem(SESSION_KEY)).toBeNull();
  });

  it("uses the current time when now is omitted", () => {
    writeSession(makeSession(new Date(Date.now() + 60_000).toISOString()));
    expect(readSession()).not.toBeNull();
    writeSession(makeSession(new Date(Date.now() - 60_000).toISOString()));
    expect(readSession()).toBeNull();
  });
});

describe("readSession with corrupted data", () => {
  it("removes an entry with invalid JSON", () => {
    localStorage.setItem(SESSION_KEY, "{not json");
    expect(readSession(NOW)).toBeNull();
    expect(localStorage.getItem(SESSION_KEY)).toBeNull();
  });

  const validExpiry = "2025-01-01T01:00:00.000Z";
  const wrongShapes: Array<[string, unknown]> = [
    ["null", null],
    ["a string", "text"],
    ["an array", []],
    ["a number", 42],
    ["empty object", {}],
    [
      "missing token",
      { expiresAt: validExpiry, user: { loginId: "a", name: "n", role: "member" } },
    ],
    [
      "empty token",
      { token: "", expiresAt: validExpiry, user: { loginId: "a", name: "n", role: "member" } },
    ],
    [
      "non-string token",
      { token: 1, expiresAt: validExpiry, user: { loginId: "a", name: "n", role: "member" } },
    ],
    [
      "missing expiresAt",
      { token: "t", user: { loginId: "a", name: "n", role: "member" } },
    ],
    ["missing user", { token: "t", expiresAt: validExpiry }],
    ["user is an array", { token: "t", expiresAt: validExpiry, user: [] }],
    [
      "unknown role",
      { token: "t", expiresAt: validExpiry, user: { loginId: "a", name: "n", role: "root" } },
    ],
    [
      "missing loginId",
      { token: "t", expiresAt: validExpiry, user: { name: "n", role: "member" } },
    ],
    [
      "missing name",
      { token: "t", expiresAt: validExpiry, user: { loginId: "a", role: "member" } },
    ],
  ];

  it.each(wrongShapes)("removes an entry with the wrong shape: %s", (_label, value) => {
    localStorage.setItem(SESSION_KEY, JSON.stringify(value));
    expect(readSession(NOW)).toBeNull();
    expect(localStorage.getItem(SESSION_KEY)).toBeNull();
  });
});

describe("purgeLegacyStorage", () => {
  it("removes the legacy shared token key", () => {
    localStorage.setItem("mablop-token", "shared-secret");
    purgeLegacyStorage();
    expect(localStorage.getItem("mablop-token")).toBeNull();
  });

  it("removes mablop-last-draft and every key with that prefix", () => {
    localStorage.setItem("mablop-last-draft", "d0");
    localStorage.setItem("mablop-last-draft:alice", "d1");
    localStorage.setItem("mablop-last-draft:bob", "d2");
    localStorage.setItem("mablop-last-draft-extra", "d3");
    purgeLegacyStorage();
    expect(localStorage.length).toBe(0);
  });

  it("keeps mablop-session and mablop-grant-id:* entries", () => {
    const session = makeSession("2025-01-01T01:00:00.000Z");
    writeSession(session);
    localStorage.setItem("mablop-grant-id:alice", "g1");
    localStorage.setItem("mablop-grant-id:bob", "g2");
    localStorage.setItem("mablop-token", "shared-secret");
    localStorage.setItem("mablop-last-draft", "d0");
    localStorage.setItem("mablop-last-draft:alice", "d1");

    purgeLegacyStorage();

    expect(readSession(NOW)).toEqual(session);
    expect(localStorage.getItem("mablop-grant-id:alice")).toBe("g1");
    expect(localStorage.getItem("mablop-grant-id:bob")).toBe("g2");
    expect(localStorage.getItem("mablop-token")).toBeNull();
    expect(localStorage.getItem("mablop-last-draft")).toBeNull();
    expect(localStorage.getItem("mablop-last-draft:alice")).toBeNull();
    expect(localStorage.length).toBe(3);
  });

  it("keeps unrelated keys", () => {
    localStorage.setItem("other-key", "v");
    localStorage.setItem("mablop-last", "v");
    purgeLegacyStorage();
    expect(localStorage.getItem("other-key")).toBe("v");
    expect(localStorage.getItem("mablop-last")).toBe("v");
  });

  it("is a no-op on empty storage and is idempotent", () => {
    expect(() => purgeLegacyStorage()).not.toThrow();
    localStorage.setItem("mablop-last-draft:alice", "d1");
    purgeLegacyStorage();
    purgeLegacyStorage();
    expect(localStorage.length).toBe(0);
  });
});
