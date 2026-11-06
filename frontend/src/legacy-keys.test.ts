import { describe, expect, test } from "vitest";

// Static guard for Req 10.2, 10.3, 23.10, 23.11: the old shared-token key
// ("mablop-token") and the draft-copy key ("mablop-last-draft") exist only in
// frontend/src/auth/session.ts, and only for removal (purgeLegacyStorage).
// No TokenField component or secret-token input remains.
//
// Same raw-scan approach as no-sdk.test.ts: read every TS/TSX source as text and
// strip comments before matching, so prose that mentions a name does not trip it.

const sources = import.meta.glob("./**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const SESSION_PATH = "./auth/session.ts";

function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/.*$/gm, "$1");
}

function basename(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

// Excluded from the string scans:
//  - *.test.ts / *.test.tsx: tests (including this one) must name the legacy keys
//    to seed and verify the purge.
//  - auth/session.ts: the single allowed home of the two legacy key constants;
//    section (e) below checks it separately.
const scanned = Object.entries(sources)
  .filter(([path]) => !/\.test\.tsx?$/.test(path) && path !== SESSION_PATH)
  .map(([path, src]) => [path, stripComments(src)] as const);

function offendersMatching(re: RegExp): string[] {
  return scanned.filter(([, code]) => re.test(code)).map(([path]) => path);
}

describe("legacy keys are absent from application sources", () => {
  test("scan covers real source files", () => {
    expect(scanned.length).toBeGreaterThan(5);
    expect(sources[SESSION_PATH]).toBeDefined();
  });

  test("(a) no 'mablop-token' string outside session.ts", () => {
    const offenders = offendersMatching(/mablop-token/);
    expect(offenders, `found in: ${offenders.join(", ")}`).toEqual([]);
  });

  test("(b) no 'mablop-last-draft' string outside session.ts", () => {
    const offenders = offendersMatching(/mablop-last-draft/);
    expect(offenders, `found in: ${offenders.join(", ")}`).toEqual([]);
  });

  test("(c) no TokenField identifier or file", () => {
    const offenders = offendersMatching(/\bTokenField\b/);
    expect(offenders, `identifier found in: ${offenders.join(", ")}`).toEqual([]);

    const files = Object.keys(sources).filter((p) => /^TokenField\b/.test(basename(p)));
    expect(files, `file found: ${files.join(", ")}`).toEqual([]);
    expect(sources["./TokenField.tsx"]).toBeUndefined();
  });

  test("(d) no localStorage access with the mablop-token key", () => {
    const access =
      /localStorage\s*(?:\.\s*\w+Item\s*\(|\[)\s*["'`]mablop-token/;
    const offenders = offendersMatching(access);
    expect(offenders, `found in: ${offenders.join(", ")}`).toEqual([]);
  });
});

describe("session.ts holds each legacy key once, for removal only", () => {
  const code = stripComments(sources[SESSION_PATH] ?? "");

  // Cut purgeLegacyStorage out (its body ends at the first column-0 closing brace).
  const start = code.indexOf("export function purgeLegacyStorage");
  const end = start === -1 ? -1 : code.indexOf("\n}", start);
  const body = start === -1 || end === -1 ? "" : code.slice(start, end + 2);
  const outside = body === "" ? code : code.replace(body, "");

  const count = (text: string, needle: string): number => text.split(needle).length - 1;

  test("(e) each legacy key literal appears exactly once", () => {
    expect(count(code, '"mablop-token"')).toBe(1);
    expect(count(code, '"mablop-last-draft"')).toBe(1);
    expect(code.match(/mablop-token/g)).toHaveLength(1);
    expect(code.match(/mablop-last-draft/g)).toHaveLength(1);
  });

  test("(e) purgeLegacyStorage exists", () => {
    expect(body).not.toBe("");
  });

  test("(e) each legacy constant is declared once and used only in purgeLegacyStorage", () => {
    for (const name of ["LEGACY_TOKEN_KEY", "LEGACY_DRAFT_KEY_PREFIX"]) {
      // Outside the purge function the name appears only in its declaration.
      expect(count(outside, name), `${name} outside purgeLegacyStorage`).toBe(1);
      expect(outside).toMatch(new RegExp(`const\\s+${name}\\s*=`));
      expect(count(body, name), `${name} inside purgeLegacyStorage`).toBeGreaterThanOrEqual(1);
    }
  });

  test("(e) legacy constants are never read or written, only removed or prefix-matched", () => {
    expect(code).not.toMatch(/(?:getItem|setItem)\s*\(\s*LEGACY_/);
    expect(body).toMatch(/removeItem\s*\(\s*LEGACY_TOKEN_KEY\s*\)/);
    expect(body).toMatch(/startsWith\s*\(\s*LEGACY_DRAFT_KEY_PREFIX\s*\)/);
    expect(body).not.toMatch(/setItem|getItem\s*\(\s*LEGACY_/);
  });
});
