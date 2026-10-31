/**
 * The vendored PDF.js, in a process without the builtins Safari 17.4 lacks.
 *
 * Nothing in CI runs Safari. Every builtin removed below is one that Chrome and
 * Firefox have and Safari 17.4 does not, and PDF.js calls most of them without
 * checking. The package's default build therefore stops in Safari 17.4 at load
 * (`Can't find variable: Iterator`); the legacy build, which
 * `vendor/pdfjs/PINNED.md` pins, brings its own. The worker runs in a process of
 * its own, since after the main file the main file's polyfills would hide the
 * worker's lack of them.
 */
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import test from "node:test";
import { promisify } from "node:util";

const VENDOR = new URL("../runtime/vendor/pdfjs/", import.meta.url);
const MAIN = new URL("pdf.min.mjs", VENDOR).href;
const WORKER = new URL("pdf.worker.min.mjs", VENDOR).href;

const REMOVE = `
  const iterator = Object.getPrototypeOf(Object.getPrototypeOf([][Symbol.iterator]()));
  for (const key of ["constructor", Symbol.toStringTag, "drop", "every", "filter", "find", "flatMap", "forEach",
    "includes", "join", "map", "reduce", "some", "take", "toArray"]) {
    delete iterator[key];
  }
  delete globalThis.Iterator;
  delete Promise.try;
  delete URL.parse;
  delete Math.sumPrecise;
  for (const owner of [Map.prototype, WeakMap.prototype]) {
    delete owner.getOrInsert;
    delete owner.getOrInsertComputed;
  }
  for (const name of ["setFromBase64", "setFromHex", "toBase64", "toHex"]) {
    delete Uint8Array.prototype[name];
  }
  delete Uint8Array.fromBase64;
  delete Uint8Array.fromHex;
  delete Response.prototype.bytes;
  delete Blob.prototype.bytes;
  delete globalThis.Float16Array;
  // Browser APIs, in Safari as everywhere, that the main file reaches at load and Node has none of.
  globalThis.DOMMatrix ??= class {};
  globalThis.Path2D ??= class {};
  globalThis.ImageData ??= class {};
`;

/** What the removal took away and a file has to bring back. */
const RESTORED = `
  const restored = () => ({
    Iterator: typeof globalThis.Iterator,
    map: typeof [].values().map,
    some: typeof [].values().some,
    promiseTry: typeof Promise.try,
    urlParse: typeof URL.parse,
    sumPrecise: typeof Math.sumPrecise,
    getOrInsertComputed: typeof Map.prototype.getOrInsertComputed,
    toHex: typeof Uint8Array.prototype.toHex,
    fromBase64: typeof Uint8Array.fromBase64,
  });
`;

const ALL_FUNCTIONS = {
  Iterator: "function",
  map: "function",
  some: "function",
  promiseTry: "function",
  urlParse: "function",
  sumPrecise: "function",
  getOrInsertComputed: "function",
  toHex: "function",
  fromBase64: "function",
};

const NONE = Object.fromEntries(Object.keys(ALL_FUNCTIONS).map((key) => [key, "undefined"]));

/** One page with a line of text; PDF.js finds the objects by scanning, as there is no xref table. */
const PDF = [
  "%PDF-1.4",
  "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
  "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
  "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R",
  "/Resources << /Font << /F1 5 0 R >> >> >> endobj",
  "4 0 obj << /Length 37 >> stream",
  "BT /F1 12 Tf 20 100 Td (Safari) Tj ET",
  "endstream endobj",
  "5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj",
  "trailer << /Root 1 0 R >>",
].join("\n");

async function inFreshProcess(body) {
  const script = `${REMOVE}${RESTORED}const before = restored();${body}`;
  const { stdout } = await promisify(execFile)(process.execPath, ["--input-type=module", "--eval", script]);
  return JSON.parse(stdout.trim().split("\n").at(-1));
}

test("the vendored worker loads by itself without what Safari 17.4 lacks", async () => {
  const { before, after, exported } = await inFreshProcess(`
    const worker = await import(${JSON.stringify(WORKER)});
    console.log(JSON.stringify({ before, after: restored(), exported: typeof worker.WorkerMessageHandler }));
  `);
  assert.deepEqual(before, NONE, "the removal has to work, or this test proves nothing");
  assert.deepEqual(after, ALL_FUNCTIONS);
  assert.equal(exported, "function");
});

test("the vendored PDF.js opens a document without what Safari 17.4 lacks", async () => {
  const { before, after, pages, fingerprint, operators } = await inFreshProcess(`
    const pdfjs = await import(${JSON.stringify(MAIN)});
    const after = restored();
    pdfjs.GlobalWorkerOptions.workerSrc = ${JSON.stringify(WORKER)};
    const data = new TextEncoder().encode(${JSON.stringify(PDF)});
    const task = pdfjs.getDocument({ data, isEvalSupported: false, verbosity: 0 });
    const pdf = await task.promise;
    const page = await pdf.getPage(1);
    const { fnArray } = await page.getOperatorList();
    console.log(JSON.stringify({ before, after, pages: pdf.numPages, fingerprint: pdf.fingerprints[0], operators: fnArray.length }));
    await task.destroy();
  `);
  assert.deepEqual(before, NONE, "the removal has to work, or this test proves nothing");
  assert.deepEqual(after, ALL_FUNCTIONS);
  assert.equal(pages, 1);
  assert.match(fingerprint, /^[0-9a-f]{32}$/);
  assert.ok(operators > 0);
});
