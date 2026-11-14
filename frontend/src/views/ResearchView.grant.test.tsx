import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ResearchView } from "./ResearchView";

const auth = vi.hoisted(() => ({
  user: null as { loginId: string; name: string; role: "member" | "admin" } | null,
}));
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    user: auth.user,
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
    register: vi.fn(),
    refresh: vi.fn(),
  }),
}));

const GRANT = {
  id: "g1",
  status: "approved",
  purpose: "테스트",
  requestedAt: "2025-01-01T00:00:00.000Z",
  decidedAt: "2025-01-02T00:00:00.000Z",
  expiresAt: "2999-01-01T00:00:00.000Z",
  expired: false,
};

let calls: Record<string, unknown>[];

beforeEach(() => {
  localStorage.clear();
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init: { body: string }) => {
      const body = JSON.parse(init.body) as Record<string, unknown>;
      let data: unknown;
      if (body.action === "grant-list-mine") data = { grants: [GRANT] };
      else {
        calls.push(body);
        data =
          body.action === "list-research-runs"
            ? { runs: [] }
            : {
                runId: "run_1",
                status: "completed",
                summary: { facts: [], generatedInterpretation: [], unverified: [], partial: false },
                sources: [],
                usage: {},
                budget: {},
                cacheHit: false,
                controls: [],
              };
      }
      return { ok: true, status: 200, json: async () => data };
    }),
  );
});afterEach(() => vi.unstubAllGlobals());

const fill = () => {
  for (let i = 0; i < 3; i++) {
    fireEvent.change(screen.getByLabelText(`정보 수집 링크 ${i + 1}`), { target: { value: `https://s${i}.example/p` } });
  }
};

test("member: run-research needs a selected grant and sends grantId", async () => {
  auth.user = { loginId: "m1", name: "회원", role: "member" };
  render(<ResearchView />);
  const submit = screen.getByRole("button", { name: "자료 수집하기" }) as HTMLButtonElement;
  expect(submit.disabled).toBe(true);

  const select = (await screen.findByRole("combobox")) as HTMLSelectElement;
  await waitFor(() => expect(select.options.length).toBeGreaterThan(1));
  fireEvent.change(select, { target: { value: "g1" } });
  expect(submit.disabled).toBe(false);

  fill();
  fireEvent.click(submit);
  await screen.findByText(/실행 상태: 완료/);
  expect(calls.find((c) => c.action === "run-research")!.grantId).toBe("g1");
  expect(calls.filter((c) => c.action === "list-research-runs").every((c) => !("grantId" in c))).toBe(true);
});

test("admin: no grant select, run-research has no grantId", async () => {
  auth.user = { loginId: "boss", name: "관리자", role: "admin" };
  render(<ResearchView />);
  expect(screen.queryByRole("combobox")).toBeNull();
  fill();
  fireEvent.click(screen.getByRole("button", { name: "자료 수집하기" }));
  await screen.findByText(/실행 상태: 완료/);
  expect(calls.find((c) => c.action === "run-research")).not.toHaveProperty("grantId");
});
