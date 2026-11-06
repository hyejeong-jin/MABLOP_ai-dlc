import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { ApiRequestError } from "../api";
import { GRANT_ID_KEY_PREFIX, useGrants, type GrantOption } from "./useGrants";

// useAuth is replaced; each test chooses the user (member, admin, or none).
type TestUser = { loginId: string; name: string; role: "member" | "admin" } | null;
const auth = vi.hoisted(() => ({ user: null as TestUser }));
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    user: auth.user,
    notice: null,
    login: async () => {},
    register: async () => "",
    logout: () => {},
    replaceToken: () => {},
  }),
}));

const MEMBER: TestUser = { loginId: "member1", name: "멤버", role: "member" };
const ADMIN: TestUser = { loginId: "admin1", name: "관리자", role: "admin" };

function grant(id: string, overrides: Partial<GrantOption> = {}): GrantOption {
  return {
    id,
    status: "approved",
    purpose: null,
    requestedAt: "2025-01-01T00:00:00Z",
    decidedAt: "2025-01-02T00:00:00Z",
    expiresAt: "2999-01-01T00:00:00Z",
    expired: false,
    ...overrides,
  };
}

function mockList(grants: GrantOption[]) {
  return vi
    .spyOn(api, "postJson")
    .mockImplementation((async () => ({ grants })) as never);
}

function keyFor(loginId: string): string {
  return `${GRANT_ID_KEY_PREFIX}${loginId}`;
}

beforeEach(() => {
  auth.user = MEMBER;
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("useGrants: who sends a request (Req 21.9)", () => {
  it("loads grant-list-mine once on mount for a member", async () => {
    const spy = mockList([grant("g1")]);
    const { result } = renderHook(() => useGrants());
    expect(result.current.enabled).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith({ action: "grant-list-mine" });
  });

  it.each([
    ["admin", ADMIN],
    ["no user", null],
  ] as const)("is disabled and sends no request for %s", async (_label, user) => {
    auth.user = user;
    const spy = mockList([grant("g1")]);
    const { result } = renderHook(() => useGrants());
    await act(async () => {
      await result.current.reload();
    });
    expect(result.current.enabled).toBe(false);
    expect(result.current.options).toEqual([]);
    expect(result.current.loading).toBe(false);
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("useGrants: options (Req 21.2, Property 38)", () => {
  it("offers exactly the approved, unexpired grants in the returned order", async () => {
    mockList([
      grant("a-ok"),
      grant("b-pending", { status: "pending", decidedAt: null, expiresAt: null }),
      grant("c-expired", { expired: true }),
      grant("d-rejected", { status: "rejected" }),
      grant("e-revoked", { status: "revoked" }),
      grant("f-ok"),
      grant("g-approved-expired", { status: "approved", expired: true }),
    ]);
    const { result } = renderHook(() => useGrants());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.options.map((g) => g.id)).toEqual(["a-ok", "f-ok"]);
  });

  it("holds for every status x expired combination", async () => {
    const statuses = ["pending", "approved", "rejected", "revoked", "unknown"];
    const grants: GrantOption[] = [];
    for (const status of statuses) {
      for (const expired of [true, false]) {
        grants.push(grant(`${status}-${expired}`, { status, expired }));
      }
    }
    mockList(grants);
    const { result } = renderHook(() => useGrants());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.options.map((g) => g.id)).toEqual(["approved-false"]);
  });

  it("treats a malformed response as no options", async () => {
    vi.spyOn(api, "postJson").mockImplementation((async () => ({})) as never);
    const { result } = renderHook(() => useGrants());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.options).toEqual([]);
    expect(result.current.error).toBeNull();
  });

  it("shows the Backend message when the request fails with an ApiRequestError", async () => {
    vi.spyOn(api, "postJson").mockRejectedValue(new ApiRequestError(500, "internal", "서버 오류"));
    const { result } = renderHook(() => useGrants());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("서버 오류");
    expect(result.current.options).toEqual([]);
    expect(result.current.selectedId).toBeNull();
  });
});

describe("useGrants: selection and storage (Req 21.4, 21.5)", () => {
  it("never selects automatically, even with a single option", async () => {
    mockList([grant("g1")]);
    const { result } = renderHook(() => useGrants());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.options).toHaveLength(1);
    expect(result.current.selectedId).toBeNull();
    expect(localStorage.getItem(keyFor("member1"))).toBeNull();
  });

  it("restores a stored id that is among the options", async () => {
    localStorage.setItem(keyFor("member1"), "g2");
    mockList([grant("g1"), grant("g2")]);
    const { result } = renderHook(() => useGrants());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.selectedId).toBe("g2");
    expect(localStorage.getItem(keyFor("member1"))).toBe("g2");
  });

  it.each([
    ["is missing from the list", [grant("g1")]],
    ["is expired", [grant("stale", { expired: true }), grant("g1")]],
    ["is not approved", [grant("stale", { status: "revoked" }), grant("g1")]],
  ])("deletes a stored id that %s and selects nothing", async (_label, grants) => {
    localStorage.setItem(keyFor("member1"), "stale");
    mockList(grants);
    const { result } = renderHook(() => useGrants());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.selectedId).toBeNull();
    expect(localStorage.getItem(keyFor("member1"))).toBeNull();
  });

  it("select stores the id under mablop-grant-id:{loginId}", async () => {
    mockList([grant("g1"), grant("g2")]);
    const { result } = renderHook(() => useGrants());
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => result.current.select("g2"));
    expect(result.current.selectedId).toBe("g2");
    expect(localStorage.getItem("mablop-grant-id:member1")).toBe("g2");
  });

  it("select(null) and select of an unlisted id clear the selection and the stored value", async () => {
    mockList([grant("g1")]);
    const { result } = renderHook(() => useGrants());
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => result.current.select("g1"));
    expect(result.current.selectedId).toBe("g1");
    act(() => result.current.select(null));
    expect(result.current.selectedId).toBeNull();
    expect(localStorage.getItem(keyFor("member1"))).toBeNull();

    act(() => result.current.select("g1"));
    act(() => result.current.select("not-listed"));
    expect(result.current.selectedId).toBeNull();
    expect(localStorage.getItem(keyFor("member1"))).toBeNull();
  });

  it("select does nothing for an admin", async () => {
    auth.user = ADMIN;
    mockList([grant("g1")]);
    const { result } = renderHook(() => useGrants());
    act(() => result.current.select("g1"));
    expect(result.current.selectedId).toBeNull();
    expect(localStorage.length).toBe(0);
  });

  it("keeps the current selection across reload while it is still usable", async () => {
    const spy = mockList([grant("g1"), grant("g2")]);
    const { result } = renderHook(() => useGrants());
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => result.current.select("g2"));
    await act(async () => {
      await result.current.reload();
    });
    expect(spy).toHaveBeenCalledTimes(2);
    expect(result.current.selectedId).toBe("g2");
  });
});

describe("useGrants: canCallBedrock", () => {
  it("is false for a member until a grant is selected", async () => {
    mockList([grant("g1")]);
    const { result } = renderHook(() => useGrants());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.canCallBedrock).toBe(false);
    act(() => result.current.select("g1"));
    expect(result.current.canCallBedrock).toBe(true);
  });

  it("is false for a member with no options", async () => {
    mockList([]);
    const { result } = renderHook(() => useGrants());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.options).toEqual([]);
    expect(result.current.canCallBedrock).toBe(false);
  });

  it.each([
    ["admin", ADMIN],
    ["no user", null],
  ] as const)("is true for %s", (_label, user) => {
    auth.user = user;
    const { result } = renderHook(() => useGrants());
    expect(result.current.canCallBedrock).toBe(true);
  });
});

// Property 38: grantId goes on a member's Bedrock_Actions only (Req 21.6, 21.9).
describe("useGrants.withGrant (Property 38)", () => {
  // Validates: Requirements 21.2, 21.6, 21.9
  const BEDROCK_ACTIONS = ["learn-style", "analyze-images", "generate-draft", "rebuild-from-stored"];

  it.each(BEDROCK_ACTIONS)("adds the selected grantId to %s for a member", async (action) => {
    mockList([grant("g1")]);
    const { result } = renderHook(() => useGrants());
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => result.current.select("g1"));
    const body = { action, extra: 1 };
    expect(result.current.withGrant(body)).toEqual({ action, extra: 1, grantId: "g1" });
    expect(body).toEqual({ action, extra: 1 });
  });

  it.each(BEDROCK_ACTIONS)("sends grantId null for %s when a member has no selection", async (action) => {
    mockList([grant("g1")]);
    const { result } = renderHook(() => useGrants());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.withGrant({ action })).toEqual({ action, grantId: null });
  });

  it.each(
    BEDROCK_ACTIONS.flatMap((action) => [
      [action, "admin", ADMIN],
      [action, "no user", null],
    ]) as [string, string, TestUser][],
  )("leaves %s unchanged for %s", (action, _label, user) => {
    auth.user = user;
    const { result } = renderHook(() => useGrants());
    const body = { action, styleId: "s1" };
    const out = result.current.withGrant(body);
    expect(out).toEqual({ action, styleId: "s1" });
    expect("grantId" in out).toBe(false);
  });
});

describe("useGrants.handleGrantError (Req 21.8)", () => {
  it("reloads and returns true for 403 forbidden", async () => {
    const spy = mockList([grant("g1")]);
    const { result } = renderHook(() => useGrants());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(spy).toHaveBeenCalledTimes(1);

    let handled = false;
    act(() => {
      handled = result.current.handleGrantError(new ApiRequestError(403, "forbidden", "권한이 없습니다."));
    });
    expect(handled).toBe(true);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.loading).toBe(false));
  });

  it("drops a selection that the reload no longer lists", async () => {
    const responses = [[grant("g1")], []];
    vi.spyOn(api, "postJson").mockImplementation((async () => ({ grants: responses.shift() ?? [] })) as never);
    const { result } = renderHook(() => useGrants());
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => result.current.select("g1"));
    act(() => {
      result.current.handleGrantError(new ApiRequestError(403, "forbidden", "만료"));
    });
    await waitFor(() => expect(result.current.options).toEqual([]));
    expect(result.current.selectedId).toBeNull();
    expect(localStorage.getItem(keyFor("member1"))).toBeNull();
  });

  it.each([
    ["403 with another code", new ApiRequestError(403, "other", "x")],
    ["401 forbidden", new ApiRequestError(401, "forbidden", "x")],
    ["500", new ApiRequestError(500, "internal", "x")],
    ["a plain Error", new Error("boom")],
    ["a non-error value", "forbidden"],
  ])("returns false and does not reload for %s", async (_label, err) => {
    const spy = mockList([grant("g1")]);
    const { result } = renderHook(() => useGrants());
    await waitFor(() => expect(result.current.loading).toBe(false));
    let handled = true;
    act(() => {
      handled = result.current.handleGrantError(err);
    });
    expect(handled).toBe(false);
    expect(spy).toHaveBeenCalledTimes(1);
  });
});

describe("useGrants: late responses are ignored", () => {
  function deferred<T>() {
    let resolve!: (v: T) => void;
    const promise = new Promise<T>((r) => {
      resolve = r;
    });
    return { promise, resolve };
  }

  it("ignores a response for the previous user after the user changes", async () => {
    const first = deferred<{ grants: GrantOption[] }>();
    const second = deferred<{ grants: GrantOption[] }>();
    const queue = [first, second];
    vi.spyOn(api, "postJson").mockImplementation((() => queue.shift()!.promise) as never);

    auth.user = { loginId: "alice", name: "A", role: "member" };
    const { result, rerender } = renderHook(() => useGrants());
    auth.user = { loginId: "bob", name: "B", role: "member" };
    rerender();

    await act(async () => {
      second.resolve({ grants: [grant("bob-grant")] });
    });
    await waitFor(() => expect(result.current.options.map((g) => g.id)).toEqual(["bob-grant"]));

    await act(async () => {
      first.resolve({ grants: [grant("alice-grant")] });
    });
    expect(result.current.options.map((g) => g.id)).toEqual(["bob-grant"]);
    expect(result.current.loading).toBe(false);
  });

  it("clears options and sends no request when the user becomes an admin", async () => {
    const spy = mockList([grant("g1")]);
    const { result, rerender } = renderHook(() => useGrants());
    await waitFor(() => expect(result.current.options).toHaveLength(1));
    auth.user = ADMIN;
    rerender();
    await waitFor(() => expect(result.current.enabled).toBe(false));
    expect(result.current.options).toEqual([]);
    expect(result.current.selectedId).toBeNull();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("ignores a response that arrives after unmount", async () => {
    const pending = deferred<{ grants: GrantOption[] }>();
    vi.spyOn(api, "postJson").mockImplementation((() => pending.promise) as never);
    localStorage.setItem(keyFor("member1"), "stale");
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    const { unmount } = renderHook(() => useGrants());
    unmount();
    await act(async () => {
      pending.resolve({ grants: [grant("g1")] });
    });

    // The late handler returned before it could delete the stored id.
    expect(localStorage.getItem(keyFor("member1"))).toBe("stale");
    expect(errorSpy).not.toHaveBeenCalled();
  });
});
