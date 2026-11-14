import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { DraftingView } from "./DraftingView";
import draftingSource from "./DraftingView.tsx?raw"; // raw source for the static SDK check
import * as api from "../api";
import * as images from "../images";

// The view reads the session user through useAuth(); the tests choose the role.
const auth = vi.hoisted(() => ({
  user: null as { loginId: string; name: string; role: "member" | "admin" } | null,
}));
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

const GRANT = {
  id: "g1",
  status: "approved",
  purpose: null,
  requestedAt: "2025-01-01T00:00:00Z",
  decidedAt: "2025-01-02T00:00:00Z",
  expiresAt: "2025-02-01T00:00:00Z",
  expired: false,
};

async function selectGrant() {
  await screen.findByRole("option", { name: /^AI 서비스 토큰 사용 권한 · / });
  fireEvent.change(screen.getByLabelText("AI 서비스 토큰 사용 권한"), { target: { value: "g1" } });
}

// resizeImage uses canvas (not functional in jsdom) -> stub to a tiny blob.
beforeEach(() => {
  auth.user = { loginId: "member1", name: "멤버", role: "member" };
  vi.spyOn(images, "resizeImage").mockImplementation(
    async (file: Blob) =>
      new Blob([new Uint8Array([1, 2, 3])], {
        type: (file as File).type || "image/jpeg",
      }),
  );
});

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

// Wire postJson to walk the 3 backend actions in order; capture their payloads.
// grant-list-mine (from useGrants) is answered but not captured: it is not a drafting step.
// get-usage (lite members only) reports 2 of 5 lite drafts used this month.
function wireApi(grants: unknown[] = [GRANT]) {
  const calls: Record<string, unknown>[] = [];
  const postJson = vi.spyOn(api, "postJson").mockImplementation(async (body) => {
    if (body.action === "grant-list-mine") return { grants } as never;
    calls.push(body);
    switch (body.action) {
      case "get-usage":
        return { liteDrafts: { used: 2, limit: 5 } } as never;
      case "issue-presigned-urls": {
        const files = body.files as { filename: string }[];
        return {
          uploads: files.map((_, i) => ({
            key: `images/img${i}.jpg`,
            url: `https://s3.example/put/${i}`,
            method: "PUT",
          })),
        } as never;
      }
      case "analyze-images":
        return { captions: [] } as never;
      case "generate-draft":
        return {
          draftId: "draft-1",
          markdown: "# 제목\n\n본문 ![img-1]",
        } as never;
      default:
        return {} as never;
    }
  });
  return { calls, postJson };
}

function makeFile(name: string): File {
  return new File([new Uint8Array([9, 9, 9])], name, { type: "image/jpeg" });
}

function attach(files: File[]) {
  const input = screen.getByLabelText("이미지 첨부") as HTMLInputElement;
  fireEvent.change(input, { target: { files } });
}

// 10-image cap enforced in the UI (Req 7.4).
test("caps attached images at MAX_IMAGES", () => {
  render(<DraftingView />);
  const twelve = Array.from({ length: 12 }, (_, i) => makeFile(`p${i}.jpg`));
  attach(twelve);
  // one description field rendered per retained image
  expect(screen.getAllByLabelText(/이미지 설명/)).toHaveLength(images.MAX_IMAGES);
});

// Full upload flow: presign -> S3 PUT (raw fetch) -> analyze -> generate; keys only.
test("uploads via presigned PUT and sends only keys to the backend", async () => {
  const { calls } = wireApi();
  const setItem = vi.spyOn(Storage.prototype, "setItem");
  // global fetch stands in for the direct-to-S3 PUT.
  const fetchMock = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(new Response(null, { status: 200 }));

  const onDraftCreated = vi.fn();
  render(<DraftingView onDraftCreated={onDraftCreated} />);
  await selectGrant();
  setItem.mockClear(); // ignore the grant selection write; the draft itself must write nothing
  fireEvent.change(screen.getByLabelText("제목"), { target: { value: "제목" } });
  attach([makeFile("a.jpg"), makeFile("b.jpg")]);
  fireEvent.change(screen.getByLabelText("이미지 설명 1"), {
    target: { value: "사용자 설명" },
  });

  fireEvent.click(screen.getByRole("button", { name: "초안 생성" }));

  // generate-draft eventually produces rendered markdown.
  expect(await screen.findByLabelText("초안 결과")).toHaveTextContent("본문");

  // S3 PUT went via raw fetch (not api.ts), with blob bodies.
  expect(fetchMock).toHaveBeenCalledTimes(2);
  for (const call of fetchMock.mock.calls) {
    const init = call[1] as RequestInit;
    expect(init.method).toBe("PUT");
    expect(init.body).toBeInstanceOf(Blob);
  }

  // the new draftId is handed to the parent once
  expect(onDraftCreated).toHaveBeenCalledTimes(1);
  expect(onDraftCreated).toHaveBeenCalledWith("draft-1");
  // nothing is written to localStorage (no token, no last draft)
  expect(setItem).not.toHaveBeenCalled();
  expect(localStorage.length).toBe(1); // only the grant selection from before the run

  // backend saw all three actions, in order.
  const actions = calls.map((c) => c.action);
  expect(actions).toEqual([
    "issue-presigned-urls",
    "analyze-images",
    "generate-draft",
  ]);

  // analyze-images carries keys (+ optional desc) only -- no File/Blob/binary.
  const analyze = calls.find((c) => c.action === "analyze-images")!;
  const analyzeImages = analyze.images as Record<string, unknown>[];
  expect(analyzeImages).toEqual([
    { key: "images/img0.jpg" },
    { key: "images/img1.jpg", description: "사용자 설명" },
  ]);

  // Bedrock actions carry the selected grantId; issue-presigned-urls does not
  const presignCall = calls.find((c) => c.action === "issue-presigned-urls")!;
  expect(presignCall).not.toHaveProperty("grantId");
  expect(analyze.grantId).toBe("g1");
  expect(calls.find((c) => c.action === "generate-draft")!.grantId).toBe("g1");

  // generate-draft carries imageKeys only; nothing in the payload is a File/Blob.
  const gen = calls.find((c) => c.action === "generate-draft")!;
  expect(gen.imageKeys).toEqual(["images/img0.jpg", "images/img1.jpg"]);
  for (const c of calls) {
    for (const v of Object.values(c)) {
      expect(v).not.toBeInstanceOf(Blob);
      expect(v).not.toBeInstanceOf(File);
    }
  }
});

// generate-draft works with no images; renders returned markdown (synchronous UX).
test("generates a draft with no images and renders markdown", async () => {
  const { calls } = wireApi();
  render(<DraftingView />);
  await selectGrant();
  fireEvent.change(screen.getByLabelText("제목"), { target: { value: "x" } });
  fireEvent.click(screen.getByRole("button", { name: "초안 생성" }));

  expect(await screen.findByLabelText("초안 결과")).toHaveTextContent("본문");
  const actions = calls.map((c) => c.action);
  expect(actions).toEqual(["generate-draft"]);
  expect((calls[0].imageKeys as string[])).toEqual([]);
});

// Design Principle 1 / Req 6.1: the view must never import a Bedrock/AWS SDK.
test("imports no Bedrock/AWS SDK", () => {
  // only inspect import lines (comments may mention Bedrock/AWS SDK by name).
  const importLines = draftingSource
    .split("\n")
    .filter((line: string) => /^\s*import\b/.test(line))
    .join("\n");
  expect(importLines).not.toMatch(/@aws-sdk|aws-sdk|bedrock/i);
});

// the token field is gone; calls take the body only (api.ts attaches the session token)
test("renders a grant select and no token field; postJson gets no token argument", async () => {
  const { postJson } = wireApi();
  render(<DraftingView />);
  expect(screen.queryByLabelText("토큰")).not.toBeInTheDocument();
  await selectGrant();
  fireEvent.change(screen.getByLabelText("제목"), { target: { value: "x" } });
  fireEvent.click(screen.getByRole("button", { name: "초안 생성" }));
  await screen.findByLabelText("초안 결과");
  for (const call of postJson.mock.calls) expect(call).toHaveLength(1);
});

// Changed for 작업 15.3: a member with a Grant gets the first owned Grant selected by default,
// so the button is enabled without a manual choice. The button waits only for the Grant list.
test("member with a Grant: 초안 생성 is enabled with the first Grant selected by default", async () => {
  const { calls } = wireApi();
  render(<DraftingView />);
  const button = screen.getByRole("button", { name: "초안 생성" });
  expect(button).toBeDisabled(); // grant list still loading
  await screen.findByRole("option", { name: /^AI 서비스 토큰 사용 권한 · / });
  await waitFor(() => expect(button).toBeEnabled());
  expect(screen.getByLabelText("AI 서비스 토큰 사용 권한")).toHaveValue("g1");
  expect(calls).toHaveLength(0);
});

// 작업 15.3 (a): no Grant -> lite notice with this month's remaining count; the button stays enabled.
test("member without a Grant sees the lite notice with this month's remaining count", async () => {
  const { calls } = wireApi([]);
  render(<DraftingView />);
  const button = screen.getByRole("button", { name: "초안 생성" });
  await waitFor(() =>
    expect(screen.getByText(/저품질 초안으로 작성돼요/)).toHaveTextContent(
      "AI 서비스 토큰이 없어 저품질 초안으로 작성돼요. 이번 달 남은 횟수: 3회",
    ),
  );
  expect(button).toBeEnabled();
  expect(calls.map((c) => c.action)).toEqual(["get-usage"]);
});

// 작업 15.3 (b): a Grant holder sends the first owned grantId without touching the select.
test("member with Grants sends the first owned grantId by default", async () => {
  const { calls } = wireApi([GRANT, { ...GRANT, id: "g2" }]);
  render(<DraftingView />);
  const select = await screen.findByLabelText("AI 서비스 토큰 사용 권한");
  await waitFor(() => expect(select).toHaveValue("g1"));
  fireEvent.click(screen.getByRole("button", { name: "초안 생성" }));
  await screen.findByLabelText("초안 결과");
  expect(calls.find((c) => c.action === "generate-draft")).toHaveProperty("grantId", "g1");
});

// 작업 15.3 (c): 429 quota_exceeded shows the server message as is, in role=alert.
test("a 429 quota_exceeded shows the server message in an alert", async () => {
  const message =
    "이번 달 저품질 초안 한도를 모두 사용했습니다. 다음 달에 다시 이용하거나 Bedrock 사용 권한을 신청하세요.";
  const { postJson } = wireApi([]);
  const base = postJson.getMockImplementation()!;
  postJson.mockImplementation((async (body: Record<string, unknown>) => {
    if (body.action === "generate-draft") throw new api.ApiRequestError(429, "quota_exceeded", message);
    return base(body as never);
  }) as never);
  render(<DraftingView />);
  await waitFor(() => expect(screen.getByText(/저품질 초안으로 작성돼요/)).toHaveTextContent("3회"));
  fireEvent.click(screen.getByRole("button", { name: "초안 생성" }));

  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toBe(message);
  expect(screen.getByText(/저품질 초안으로 작성돼요/)).toHaveTextContent("이번 달 남은 횟수: 0회");
});

// 작업 15.3: a lite result carries the 저품질 초안 text badge; the request has no grantId.
test("a lite draft shows the 저품질 초안 badge and the updated remaining count", async () => {
  const { calls, postJson } = wireApi([]);
  const base = postJson.getMockImplementation()!;
  postJson.mockImplementation((async (body: Record<string, unknown>) =>
    body.action === "generate-draft"
      ? { draftId: "d-lite", markdown: "# 제목\n\n본문", tier: "lite", liteRemaining: 2 }
      : base(body as never)) as never);
  render(<DraftingView />);
  await waitFor(() => expect(screen.getByText(/저품질 초안으로 작성돼요/)).toHaveTextContent("3회"));
  fireEvent.click(screen.getByRole("button", { name: "초안 생성" }));

  expect(await screen.findByLabelText("초안 결과")).toBeInTheDocument();
  expect(screen.getByText("저품질 초안")).toBeInTheDocument();
  expect(screen.queryByText("고품질 초안")).not.toBeInTheDocument();
  expect(screen.getByText(/저품질 초안으로 작성돼요/)).toHaveTextContent("이번 달 남은 횟수: 2회");
  // this override answers generate-draft itself, so read the request from the spy's call log
  const sent = postJson.mock.calls.map((c) => c[0]).find((b) => b.action === "generate-draft");
  expect(sent).toHaveProperty("grantId", null);
  expect(calls.map((c) => c.action)).toContain("get-usage");
});

// 작업 15.3: a quality result carries the 고품질 초안 text badge.
test("a quality draft shows the 고품질 초안 badge", async () => {
  const { postJson } = wireApi();
  const base = postJson.getMockImplementation()!;
  postJson.mockImplementation((async (body: Record<string, unknown>) =>
    body.action === "generate-draft"
      ? { draftId: "d-q", markdown: "# 제목\n\n본문", tier: "quality" }
      : base(body as never)) as never);
  render(<DraftingView />);
  await selectGrant();
  fireEvent.click(screen.getByRole("button", { name: "초안 생성" }));

  expect(await screen.findByLabelText("초안 결과")).toBeInTheDocument();
  expect(screen.getByText("고품질 초안")).toBeInTheDocument();
  expect(screen.queryByText("저품질 초안")).not.toBeInTheDocument();
});

// an admin needs no grant: no select, enabled button, no grantId in the body (Req 21.9)
test("admin: no grant select, button enabled, generate-draft has no grantId", async () => {
  auth.user = { loginId: "boss", name: "관리자", role: "admin" };
  const { calls } = wireApi();
  render(<DraftingView />);
  expect(screen.queryByLabelText("AI 서비스 토큰 사용 권한")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("제목"), { target: { value: "x" } });
  fireEvent.click(screen.getByRole("button", { name: "초안 생성" }));
  await screen.findByLabelText("초안 결과");
  expect(calls).toHaveLength(1);
  expect(calls[0]).not.toHaveProperty("grantId");
});

// 403 forbidden: show the backend message and reload the grant options (Req 21.8)
test("403 forbidden shows the message and reloads grant options", async () => {
  const listCalls: number[] = [];
  vi.spyOn(api, "postJson").mockImplementation((async (body: Record<string, unknown>) => {
    if (body.action === "grant-list-mine") {
      listCalls.push(1);
      return { grants: [GRANT] };
    }
    throw new api.ApiRequestError(403, "forbidden", "사용 권한이 만료되었습니다.");
  }) as never);
  render(<DraftingView />);
  await selectGrant();
  fireEvent.click(screen.getByRole("button", { name: "초안 생성" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("사용 권한이 만료되었습니다.");
  await waitFor(() => expect(listCalls.length).toBe(2));
});

test("shows the general-style notice when nothing has been learned yet", async () => {
  const { postJson } = wireApi();
  const base = postJson.getMockImplementation()!;
  postJson.mockImplementation((async (body: Record<string, unknown>) =>
    body.action === "generate-draft"
      ? { draftId: "draft-2", markdown: "# 제목\n\n본문", generalStyle: true }
      : base(body as never)) as never);

  render(<DraftingView />);
  await selectGrant();
  fireEvent.change(screen.getByLabelText("제목"), { target: { value: "제목" } });
  fireEvent.click(screen.getByRole("button", { name: "초안 생성" }));

  expect(await screen.findByLabelText("초안 결과")).toBeInTheDocument();
  expect(screen.getByText(/일반 문체로 작성되었습니다/)).toBeInTheDocument();
});

test("sends researchRunId/vocIds only when selected", async () => {
  const { calls, postJson } = wireApi();
  const base = postJson.getMockImplementation()!;
  postJson.mockImplementation((async (body: Record<string, unknown>) => {
    if (body.action === "list-research-runs")
      return { runs: [{ runId: "run-1", status: "completed", requestedAt: "2025-01-01T00:00:00.000Z" }] } as never;
    if (body.action === "list-voc")
      return { records: [{ vocId: "v1", preview: "의견하나", status: "new" }, { vocId: "v2", preview: "보관됨", status: "archived" }] } as never;
    return base(body as never);
  }) as never);
  render(<DraftingView />);
  await selectGrant();
  fireEvent.click(screen.getByRole("button", { name: "글 재료 선택하기" }));
  expect(await screen.findByText("의견하나")).toBeInTheDocument();
  expect(screen.queryByText("보관됨")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("제목"), { target: { value: "t" } });
  fireEvent.click(screen.getByRole("button", { name: "초안 생성" }));
  await screen.findByLabelText("초안 결과");
  const plain = calls.find((c) => c.action === "generate-draft")!;
  expect(plain).not.toHaveProperty("researchRunId");
  expect(plain).not.toHaveProperty("vocIds");
});

test("preselects initialResearchRunId and sends it", async () => {
  const { calls } = wireApi();
  render(<DraftingView initialResearchRunId="run-9" />);
  await selectGrant();
  fireEvent.click(screen.getByRole("button", { name: "초안 생성" }));
  await screen.findByLabelText("초안 결과");
  expect(calls.find((c) => c.action === "generate-draft")).toHaveProperty("researchRunId", "run-9");
});
