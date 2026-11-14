import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import * as api from "../api";
import { AdminView } from "./AdminView";

afterEach(() => vi.restoreAllMocks());

test("목소리함 관리: 목록, 상태 필터, 상태 변경이 admin action으로 연결된다", async () => {
  const calls: Record<string, unknown>[] = [];
  vi.spyOn(api, "postJson").mockImplementation((async (body: Record<string, unknown>) => {
    calls.push(body);
    if (body.action === "admin-list-users") return { users: [] };
    if (body.action === "admin-grant-list") return { grants: [] };
    if (body.action === "admin-list-voc") {
      return {
        records: [
          { vocId: "voc_1", ownerId: "u_1", preview: "배송이 느려요", status: "new", createdAt: "2025-01-10T03:00:00.000Z" },
        ],
      };
    }
    return {};
  }) as never);

  render(<AdminView />);
  expect(await screen.findByText("배송이 느려요")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "목소리함 관리" })).toBeTruthy();

  fireEvent.change(screen.getByLabelText("의견 상태 변경 voc_1"), { target: { value: "confirmed" } });
  await waitFor(() =>
    expect(calls.find((c) => c.action === "admin-update-voc-status")).toEqual({
      action: "admin-update-voc-status",
      userId: "u_1",
      vocId: "voc_1",
      status: "confirmed",
    }),
  );

  fireEvent.change(screen.getByLabelText("목소리함 상태 필터"), { target: { value: "reviewing" } });
  await waitFor(() =>
    expect(calls.some((c) => c.action === "admin-list-voc" && c.status === "reviewing")).toBe(true),
  );
});
