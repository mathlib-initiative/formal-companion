/**
 * Synthetic fixtures for the overlay viewer's node tests.
 *
 * Everything here is invented: Literature Links annotation files about
 * "Elementary Sets: Lecture Notes (synthetic)", their bundle and the
 * `GENERATED.json` that records it, a
 * matching `overlay.config.json`, and a DOM and PDF.js stand-in good enough for
 * the controller. No file, hash, namespace or page count of any real book or
 * companion appears.
 *
 * This file is not a test file (no `.test.mjs` suffix), so `node --test` on this
 * directory imports it but never runs it on its own.
 */
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { BUNDLE_SCHEMA, NAMESPACE, sha256Hex } from "../runtime/viewer/viewer-core.js";

export const HERE = path.dirname(fileURLToPath(import.meta.url));
export const VIEWER_DIR = path.resolve(HERE, "../runtime/viewer");

export const SOURCE_ID = "src:notes";
export const OTHER_SOURCE_ID = "src:other";
export const PDF_ROUTE = "/__overlay_pdf__/notes.pdf";
export const PDF_BYTES = new TextEncoder().encode(
  "synthetic stand-in for a source PDF; not a PDF and not any book's bytes",
);
/** Where the viewer page sits when the site is served on loopback. */
export const ORIGIN = "http://127.0.0.1:8000";
export const PAGE_URL = `${ORIGIN}/pdf-overlay/viewer/index.html`;

/** A 64-hex-digit stand-in that is nobody's real digest. */
export function fakeSha256(seed) {
  return seed.repeat(64).slice(0, 64);
}

export async function shellHtml() {
  return readFile(path.join(VIEWER_DIR, "index.html"), "utf8");
}

// --- the records ------------------------------------------------------------

export function pdfSource(sha256, { id = SOURCE_ID, pages = 12 } = {}) {
  return {
    extensions: { [NAMESPACE]: { bytes: PDF_BYTES.byteLength, physical_page_count: pages, source_id: id } },
    pin: { sha256, title: "Elementary Sets: Lecture Notes (synthetic)", type: "book",
           url: "https://notes.example.invalid/elementary-sets.pdf" },
    type: "pdf",
    uri: "https://notes.example.invalid/elementary-sets.pdf",
  };
}

function repository(module, declaration) {
  return { commit: "0".repeat(40), declaration, module, path: `${module.replaceAll(".", "/")}.lean`,
           type: "repository", url: "https://git.example.invalid/an-owner/notes-companion" };
}

/** One link record: `extra` is merged into the record, `ext` into its extension. */
function link(sha256, { id, item, region, page, rect, role, decl, module = "Notes.Box", uri, ext = {},
                        extra = {} }) {
  const record = {
    extensions: { [NAMESPACE]: { declaration: { id: decl, kind: "theorem" }, item, region, role, ...ext } },
    id,
    location: { page, rect },
    source: pdfSource(sha256),
    ...extra,
  };
  if (decl !== undefined) {
    record.target = { pin: repository(module, decl), uri };
  }
  return record;
}

const THEOREM = { item: "item:theorem-1.1", kind: "theorem", label: "Theorem 1.1", page: 2,
                  rect: [0.25, 0.125, 0.5, 0.125], region: "loc:0001", status: "formalized" };
const SHARED = { ...THEOREM, rect: [0.1, 0.4, 0.7, 0.1], region: "loc:0004" };
const CURATION = { actor: "agent", confirmed_by_human: false, method: "curation" };
const THEOREM_VERDICTS = [{
  evidence: ["Notes/Box.lean:12", "source page 2"],
  provenance: { actor: "agent", confirmed_by_human: false, method: "adjudication",
                note: "two independent readings and a reconciler" },
  resolution: { pr: 7, state: "fixed" },
  severity: "medium",
  status: "fixed",
  summary: "The box bound was stated for closed boxes only.",
  verdict: "deviation",
}];

function theoremLinks(sha256, where) {
  const base = (role, decl, rest = {}) => link(sha256, {
    decl, extra: { kind: where.kind, label: where.label, status: where.status }, id: `${where.item}/${role}/${decl}`,
    item: where.item, page: where.page, rect: where.rect, region: where.region, role, ...rest,
    ext: { locator: { physical: [2], text: "Chapter 1, Theorem 1.1" }, provenance: CURATION,
           verdicts: THEOREM_VERDICTS, ...(rest.ext ?? {}) },
  });
  // Deliberately not in role order: the viewer is what orders them.
  return [
    base("helper", "Notes.Set.union_comm", {
      ext: { declaration: { id: "Notes.Set.union_comm", kind: "theorem", sorry: "exercise" },
             destination: { resolution: "exact" } },
      module: "Notes.Set", uri: "../../Notes/Set/index.html#Notes___Set___union_comm",
    }),
    base("key", "Notes.Box.volume_nonneg", {
      ext: { declaration_verdict: { note: "repaired in place", status: "fixed", verdict: "faithful" },
             destination: { resolution: "exact" }, relation: "exact",
             snippet: { line: 12, text: "theorem volume_nonneg (b : Box) : 0 <= b.volume" } },
      uri: "../../Notes/Box/index.html#Notes___Box___volume_nonneg",
    }),
    base("example", "example:0123456789ab", {
      ext: { declaration: { anonymous: true, id: "example:0123456789ab", kind: "example" },
             provenance: { actor: "tool", confirmed_by_human: true, method: "pipeline" } },
      module: "Notes.Set", uri: "../../Notes/Set.lean",
    }),
    base("support", "Notes.Set.inter_sub", {
      ext: { destination: { resolution: "exact" } },
      module: "Notes.Set", uri: "https://notes.example.invalid/api/Notes/Set.html#Notes___Set___inter_sub",
    }),
    base("attributed", "Notes.Box.volume_mono", {
      ext: { declaration_verdict: { note: "left unchanged", status: "fixed", verdict: "deviation" },
             destination: { fallback_label: "module page (fallback)", resolution: "fallback" } },
      uri: "../../Notes/Box/index.html",
    }),
  ];
}

/**
 * The records every test starts from, by section folder: "1" holds the first
 * two sections' records and "2" the rest, each record one annotation file
 * (`annotationFiles`).
 */
export function makeSections(sha256) {
  const definition = link(sha256, {
    decl: "Notes.Set.inter_sub",
    ext: { destination: { resolution: "exact" }, locator: { physical: [3], printed: [1] },
           quads: [[0.1, 0.2, 0.9, 0.2, 0.9, 0.3, 0.1, 0.3]] },
    extra: { kind: "definition", label: "Definition 2.1",
             note: "The companion states the neighbouring box lemma, not this definition.",
             status: "support_only" },
    id: "item:definition-2.1/support/Notes.Set.inter_sub", item: "item:definition-2.1", module: "Notes.Set",
    page: 3, rect: [0.1, 0.2, 0.8, 0.1], region: "loc:0002", role: "support",
    uri: "https://notes.example.invalid/api/Notes/Set.html#Notes___Set___inter_sub",
  });
  const unlinked = {
    extensions: { [NAMESPACE]: { clusters: ["cluster:sets"], item: "item:exercise-3.1", region: "loc:0003" } },
    id: "item:exercise-3.1", kind: "exercise", label: "Exercise 3.1",
    location: { page: 5, rect: [0.1, 0.1, 0.4, 0.1] },
    source: pdfSource(sha256),
  };
  const remark = link(sha256, {
    decl: "Notes.Internal.aux",
    ext: { declaration: { id: "private:Notes.Internal:Notes.Internal.aux", kind: "lemma", private: true },
           destination: { resolution: "exact" } },
    extra: { kind: "passage", label: "A remark on unions", status: "formalized" },
    id: "item:remark-4.1/key/private", item: "item:remark-4.1", module: "Notes.Internal",
    page: 2, rect: SHARED.rect, region: "loc:0004", role: "key",
    uri: "../../Notes/Internal/index.html#Notes___Internal___aux",
  });
  // One region, two items: the curation names the remark first, and the
  // equation split off its label (no card) comes first in file order.
  const pair = { region_items: ["item:remark-7.1", "item:equation-7"] };
  const equation = {
    extensions: { [NAMESPACE]: { ...pair, item: "item:equation-7", region: "loc:0005" } },
    id: "item:equation-7", kind: "equation", label: "Equation 7",
    location: { page: 7, rect: [0.1, 0.5, 0.8, 0.1] },
    source: pdfSource(sha256),
  };
  const remarkSeven = link(sha256, {
    decl: "Notes.Box.volume_nonneg",
    ext: { ...pair, destination: { resolution: "exact" }, provenance: CURATION },
    extra: { kind: "remark", label: "Remark 7.1 and equation (7)", status: "formalized" },
    id: "item:remark-7.1/key/Notes.Box.volume_nonneg", item: "item:remark-7.1",
    page: 7, rect: [0.1, 0.5, 0.8, 0.1], region: "loc:0005", role: "key",
    uri: "../../Notes/Box/index.html#Notes___Box___volume_nonneg",
  });
  // A record from elsewhere: no overlay extension at all.
  const plain = {
    id: "plain-link", label: "Lemma 6.1", location: { page: 6, rect: [0.2, 0.2, 0.2, 0.2] },
    source: pdfSource(sha256), target: "../../Notes/Plain/index.html",
  };
  plain.source = { ...plain.source };
  // A record of another PDF: not this overlay's business.
  const other = { ...unlinked, id: "elsewhere", source: pdfSource(fakeSha256("e"), { id: OTHER_SOURCE_ID }) };
  return {
    1: [...theoremLinks(sha256, THEOREM), definition, other],
    2: [unlinked, remark, ...theoremLinks(sha256, SHARED), plain, equation, remarkSeven],
  };
}

/** `[{path, annotation}]`: `annotations/<section>/<nn>.json`, one per record, in file order. */
export function annotationFiles(sections) {
  return Object.entries(sections).flatMap(([section, records]) => records.map((annotation, index) => (
    { annotation, path: `annotations/${section}/${String(index).padStart(2, "0")}.json` })));
}

/** The bundle `GENERATED.json` names, beside it, and the URL the viewer fetches it from. */
export const BUNDLE_PATH = "annotations.bundle.json";
export const BUNDLE_URL = `${ORIGIN}/pdf-overlay/${BUNDLE_PATH}`;

export function bundleText(sections) {
  const files = annotationFiles(sections);
  return `${JSON.stringify({ annotations: files, count: files.length, directory: "annotations",
                             schema: BUNDLE_SCHEMA, schema_version: 1 })}\n`;
}

/** `GENERATED.json`'s annotations block for this bundle, with its real sha256. */
export async function makeGenerated(sections, text = bundleText(sections)) {
  return {
    annotations: { bundle: { path: BUNDLE_PATH, sha256: await sha256Hex(new TextEncoder().encode(text)) },
                   count: annotationFiles(sections).length, directory: "annotations",
                   manifest_sha256: fakeSha256("m") },
    schema: "formal-companion/overlay-generated",
  };
}

/** What `loadAnnotations` hands `buildIndex`: each record with the URL of its own file. */
export function fileInputs(sections) {
  return annotationFiles(sections).map(({ annotation, path }) => ({ path, records: [annotation],
                                                                   url: `${ORIGIN}/${path}` }));
}

// --- the configuration ------------------------------------------------------

export function makeConfig(overrides = {}) {
  return {
    back_link: { href: "../../index.html", text: "Back to the notes" },
    display: { strip_prefix: "Notes." },
    lede: "Regions on the source, declarations in the companion.",
    pdf: {
      cache_dir: ".lake/overlay-pdf",
      file_name: "notes.pdf",
      route: PDF_ROUTE,
    },
    schema: "formal-companion/overlay-config",
    schema_version: 1,
    serve: { host: "127.0.0.1", port: 8000 },
    site: {
      index: "index.html",
      link_text: "Source overlay",
      mount: "pdf-overlay",
      output_root: ".lake/build/literate-html",
    },
    source_id: SOURCE_ID,
    storage_key: "notes-overlay-page",
    title: "Elementary sets: source overlay (synthetic)",
    ...overrides,
  };
}

// --- a DOM stand-in ---------------------------------------------------------

export class FakeOption {
  constructor(label, value) {
    this.textContent = label;
    this.value = value;
    this.tagName = "OPTION";
  }
}

export function fakeElement(tag = "div") {
  const listeners = new Map();
  const element = {
    attributes: {},
    checked: false,
    children: [],
    className: "",
    dataset: {},
    disabled: false,
    hidden: false,
    listeners,
    style: {},
    tagName: tag.toUpperCase(),
    textContent: "",
    title: "",
    type: "",
    value: "",
    addEventListener(type, handler) {
      listeners.set(type, [...(listeners.get(type) ?? []), handler]);
    },
    append(...items) {
      this.children.push(...items);
    },
    getContext() {
      return {};
    },
    querySelectorAll(selector) {
      const want = selector.replace(/^\./, "");
      return this.children.filter((child) => String(child.className).split(" ").includes(want));
    },
    replaceChildren(...items) {
      this.children = [...items];
    },
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
  };
  const classes = () => String(element.className).split(" ").filter(Boolean);
  element.classList = {
    add(...names) {
      element.className = [...new Set([...classes(), ...names])].join(" ");
    },
    contains(name) {
      return classes().includes(name);
    },
    remove(...names) {
      element.className = classes().filter((name) => !names.includes(name)).join(" ");
    },
    toggle(name, force) {
      const on = force ?? !classes().includes(name);
      if (on) element.classList.add(name); else element.classList.remove(name);
      return on;
    },
  };
  return element;
}

/** A document whose elements are exactly the ids the shell declares. */
export function fakeDocument(html) {
  const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]);
  const elements = new Map(ids.map((id) => [id, fakeElement()]));
  const listeners = new Map();
  return {
    elements,
    listeners,
    title: "",
    addEventListener(type, handler) {
      listeners.set(type, [...(listeners.get(type) ?? []), handler]);
    },
    createElement: (tag) => fakeElement(tag),
    createElementNS: (_ns, tag) => fakeElement(tag),
    getElementById: (id) => elements.get(id) ?? null,
  };
}

export async function fire(element, type, event = {}) {
  for (const handler of element.listeners.get(type) ?? []) {
    await handler({ key: "", target: element, preventDefault() {}, ...event });
  }
}

// --- fetch and PDF.js stands-in ---------------------------------------------

export function jsonResponse(value, { contentType = "application/json", ok = true, status = 200 } = {}) {
  const bytes = value instanceof Uint8Array ? value
    : new TextEncoder().encode(typeof value === "string" ? value : JSON.stringify(value));
  return {
    arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
    headers: {
      get: (name) => {
        const key = name.toLowerCase();
        if (key === "content-type") {
          return contentType;
        }
        return key === "content-length" ? String(bytes.byteLength) : null;
      },
    },
    ok,
    status,
  };
}

export function notFound() {
  return jsonResponse(new Uint8Array(), { ok: false, status: 404 });
}

/**
 * A 600 x 800 pt page with no rotation of its own. Its viewport maps PDF space
 * (origin bottom left) to the canvas (origin top left) and back.
 */
export function fakeViewport({ rotation = 0, scale = 1 } = {}) {
  return {
    convertToPdfPoint: (x, y) => [x / scale, 800 - y / scale],
    convertToViewportPoint: (x, y) => [x * scale, (800 - y) * scale],
    height: 800 * scale,
    rotation,
    width: 600 * scale,
  };
}

export function fakePdfjs({ numPages = 12 } = {}) {
  const page = {
    getViewport: (options) => fakeViewport(options),
    render: () => ({ cancel() {}, promise: Promise.resolve() }),
    rotate: 0,
    view: [0, 0, 600, 800],
  };
  const calls = [];
  return {
    calls,
    GlobalWorkerOptions: {},
    getDocument: (options) => {
      calls.push(options);
      return { promise: Promise.resolve({ getPage: async () => page, numPages }) };
    },
  };
}

/** A storage that throws on every access, as a locked-down browser does. */
export function throwingStorage() {
  return {
    getItem() {
      throw new Error("storage is blocked");
    },
    setItem() {
      throw new Error("storage is blocked");
    },
  };
}

export function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    values,
    getItem: (key) => (values.has(key) ? values.get(key) : null),
    setItem: (key, value) => values.set(key, String(value)),
  };
}
