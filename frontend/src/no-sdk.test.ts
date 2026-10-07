import { expect, test } from "vitest";
import pkg from "../package.json";

// Design Principle 1 ("Browser never touches Bedrock") / Req 6.1: no file under
// frontend/src may import a Bedrock/AWS SDK. This is a repo-wide static guard;
// DraftingView.test.tsx covers the single-view case.

const SDK = /@aws-sdk|aws-sdk|@aws-amplify|bedrock/i;

// Raw source of every TS/TSX file under src (this test included; it only mentions
// the SDK names inside strings/comments, which we strip before matching).
const sources = import.meta.glob("./**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

// Strip comments so names mentioned in prose (e.g. "Bedrock") don't trip the check,
// then keep only import/require lines -- that is what we actually forbid.
function importLines(src: string): string {
  const noComments = src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\/\/.*$/gm, "");
  return noComments
    .split("\n")
    .filter((line) => /^\s*import\b/.test(line) || /\brequire\(/.test(line))
    .join("\n");
}

test("no file under frontend/src imports a Bedrock/AWS SDK", () => {
  const offenders = Object.entries(sources)
    .filter(([, src]) => SDK.test(importLines(src)))
    .map(([path]) => path);
  expect(offenders, `SDK import found in: ${offenders.join(", ")}`).toEqual([]);
});

test("package.json declares no Bedrock/AWS SDK dependency", () => {
  const deps = {
    ...(pkg as { dependencies?: Record<string, string> }).dependencies,
    ...(pkg as { devDependencies?: Record<string, string> }).devDependencies,
  };
  const offenders = Object.keys(deps).filter((name) => SDK.test(name));
  expect(offenders, `SDK dependency found: ${offenders.join(", ")}`).toEqual([]);
});
