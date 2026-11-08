/**
 * Delimited LaTeX in annotation labels (docs/overlay.md, Mathematical labels).
 * KaTeX produces MathML only. Authored strings never become HTML: only its
 * generated MathML is parsed, with trusted commands disabled and bounded work.
 */
import katex from "../vendor/katex/katex.mjs";

const MATHML = "http://www.w3.org/1998/Math/MathML";
const MAX_MATH_LENGTH = 4096;

function escaped(value, at) {
  let slashes = 0;
  while (at > 0 && value[--at] === "\\") slashes += 1;
  return slashes % 2 === 1;
}

/** Text/math runs; only explicit $, $$, \( and \[ delimiters request math. */
export function mathRuns(value) {
  const input = String(value ?? "");
  const runs = [];
  let start = 0;
  for (let at = 0; at < input.length; at += 1) {
    if (escaped(input, at)) continue;
    const open = ["\\(", "\\[", "$$", "$"].find((mark) => input.startsWith(mark, at));
    if (!open) continue;
    const close = open === "\\(" ? "\\)" : open === "\\[" ? "\\]" : open;
    let end = at + open.length;
    let braces = 0;
    for (; end < input.length; end += 1) {
      if (escaped(input, end)) continue;
      if (!braces && input.startsWith(close, end)) break;
      if (input[end] === "{") braces += 1;
      if (input[end] === "}") braces = Math.max(0, braces - 1);
    }
    if (end === input.length || !input.slice(at + open.length, end).trim()) continue;
    if (at > start) runs.push({ kind: "text", text: input.slice(start, at) });
    runs.push({ kind: "math", text: input.slice(at + open.length, end),
      raw: input.slice(at, end + close.length) });
    start = end + close.length;
    at = start - 1;
  }
  if (start < input.length) runs.push({ kind: "text", text: input.slice(start) });
  return runs;
}

/** A complete MathML element, or null so an invalid formula stays readable. */
export function mathMarkup(tex) {
  if (!tex || tex.length > MAX_MATH_LENGTH) return null;
  try {
    const markup = katex.renderToString(tex, {
      output: "mathml", displayMode: false, throwOnError: true,
      trust: false, strict: "error", maxExpand: 200, maxSize: 10, macros: {},
    });
    return markup.match(/<math\b[\s\S]*<\/math>/)?.[0] ?? null;
  } catch {
    return null;
  }
}

/** Import only the math root generated above, never HTML supplied by a label. */
export function mathNode(tex, documentRef, Parser = globalThis.DOMParser) {
  if (!Parser || !documentRef?.importNode) return null;
  const markup = mathMarkup(tex);
  if (!markup) return null;
  const root = new Parser().parseFromString(markup, "application/xml").documentElement;
  if (root.localName !== "math" || root.namespaceURI !== MATHML) return null;
  return documentRef.importNode(root, true);
}
