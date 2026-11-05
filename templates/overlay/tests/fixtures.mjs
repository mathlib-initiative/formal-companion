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

import { BUNDLE_SCHEMA, NAMESPACE, itemFolder, sha256Hex } from "../runtime/viewer/viewer-core.js";

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
/** The route as the viewer fetches it: resolved against its page, at the root of the origin. */
export const PDF_URL = `${ORIGIN}${PDF_ROUTE}`;
/**
 * A site served from a project path, as a static host such as GitHub Pages
 * serves a repository's site: the site root is a directory below the origin.
 */
export const PROJECT_ORIGIN = "https://an-owner.github.io";
export const PROJECT_PAGE_URL = `${PROJECT_ORIGIN}/notes-companion/pdf-overlay/viewer/index.html`;

/** A 64-hex-digit stand-in that is nobody's real digest. */
export function fakeSha256(seed) {
  return seed.repeat(64).slice(0, 64);
}

export async function shellHtml() {
  return readFile(path.join(VIEWER_DIR, "index.html"), "utf8");
}

// --- the records ------------------------------------------------------------

/** The repository the synthetic companion's ends name, at a synthetic commit. */
export const REPOSITORY = "https://git.example.invalid/an-owner/notes-companion";

/** A subject end of the PDF, with no location: what every region of it shares. */
export function pdfSource(sha256, { id = SOURCE_ID, pages = 12 } = {}) {
  return {
    citation: { id, title: "Elementary Sets: Lecture Notes (synthetic)", type: "book",
                URL: "https://notes.example.invalid/elementary-sets.pdf" },
    extensions: { [NAMESPACE]: { bytes: PDF_BYTES.byteLength, physical_page_count: pages, source_id: id } },
    sha256,
    type: "pdf",
    uri: "https://notes.example.invalid/elementary-sets.pdf",
  };
}

/** One region of the PDF as a subject end: `ext` is merged into its extension. */
function region(sha256, { page, rect, region: id, title, ext = {}, source = {} }) {
  const end = pdfSource(sha256, source);
  end.extensions[NAMESPACE] = { ...end.extensions[NAMESPACE], ...(id ? { region: id } : {}), ...ext };
  return { ...end, location: { page, rect }, title };
}

/** A declaration in the repository as an object end: `ext` is merged into its extension. */
function lean({ role, decl, module = "Notes.Box", location, ext = {} }) {
  return {
    extensions: { [NAMESPACE]: { declaration: { id: decl, kind: "theorem" }, ...ext } },
    location: location ?? { declaration: decl, module },
    role,
    type: "lean",
    uri: REPOSITORY,
    version: "0".repeat(40),
  };
}

/** The record of one link: `extra` is merged into the record, `ext` into its extension. */
function link(subject, object, { item, ext = {}, extra = {} }) {
  return { extensions: { [NAMESPACE]: ext }, id: item, object: [object], subject, ...extra };
}

const THEOREM = { item: "item:theorem-1.1", kind: "theorem", page: 2, rect: [0.25, 0.125, 0.5, 0.125],
                  region: "loc:0001", status: "formalized", title: "Theorem 1.1" };
/** A region the theorem shares with a remark, which the curation names first. */
const SHARED = { ...THEOREM, rect: [0.1, 0.4, 0.7, 0.1], region: "loc:0004",
                 ext: { region_items: ["item:remark-4.1", "item:theorem-1.1"] } };
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

/** The theorem's five links, each one record whose subject is both of its regions. */
function theoremLinks(sha256) {
  const subject = () => [region(sha256, THEOREM), region(sha256, SHARED)];
  const base = (role, decl, { end = {}, ext = {}, extra = {}, module } = {}) => link(
    subject(), lean({ decl, module, role, ...end }),
    { ext: { locator: { physical: [2], text: "Chapter 1, Theorem 1.1" }, provenance: CURATION,
             verdicts: THEOREM_VERDICTS, ...ext },
      extra: { kind: THEOREM.kind, status: THEOREM.status, ...extra }, item: THEOREM.item },
  );
  // Deliberately not in role order: the viewer is what orders them.
  return [
    base("helper", "Notes.Set.union_comm", {
      end: { ext: { declaration: { id: "Notes.Set.union_comm", kind: "theorem", sorry: "exercise" },
                    destination: { resolution: "exact" } }, module: "Notes.Set" },
    }),
    base("key", "Notes.Box.volume_nonneg", {
      end: { ext: { declaration_verdict: { note: "repaired in place", status: "fixed", verdict: "faithful" },
                    destination: { resolution: "exact" },
                    snippet: { line: 12, text: "theorem volume_nonneg (b : Box) : 0 <= b.volume" } } },
      extra: { relation: "exactMatch" },
    }),
    base("example", "example:0123456789ab", {
      end: { ext: { declaration: { anonymous: true, id: "example:0123456789ab", kind: "example" } },
             location: { lines: [30], module: "Notes.Set" } },
      ext: { provenance: { actor: "tool", confirmed_by_human: true, method: "pipeline" } },
    }),
    base("support", "Notes.Set.inter_sub", {
      end: { ext: { destination: { href: "https://notes.example.invalid/api/Notes/Set.html", resolution: "exact" } },
             module: "Notes.Set" },
    }),
    base("attributed", "Notes.Box.volume_mono", {
      end: { ext: { declaration_verdict: { note: "left unchanged", status: "fixed", verdict: "deviation" },
                    destination: { fallback_label: "module page (fallback)", resolution: "fallback" } } },
    }),
  ];
}

/** The record of a link with this item, role and declaration. */
export function linkRecord(sections, item, role, declaration) {
  return Object.values(sections).flat().find((record) => record.id === item
    && record.object?.some((end) => end.role === role && (end.location?.declaration ?? end.extensions?.[NAMESPACE]
      ?.declaration?.id) === declaration));
}

/**
 * A copy of a link record that links `name` instead: `role` and `kind` replace
 * its end's, and `drop` names extension members its end leaves out.
 */
export function relinked(record, name, { role, kind, drop = [] } = {}) {
  const copy = structuredClone(record);
  const [end] = copy.object;
  const ext = end.extensions[NAMESPACE];
  end.location = { ...end.location, declaration: name };
  ext.declaration = { id: name, kind: kind ?? ext.declaration.kind };
  if (role) {
    end.role = role;
  }
  for (const key of drop) {
    delete ext[key];
  }
  return copy;
}

/**
 * The records every test starts from, by section folder: "1" holds the first
 * two sections' records and "2" the rest, each record one annotation file
 * (`annotationFiles`).
 */
export function makeSections(sha256) {
  const definition = link(
    [region(sha256, { ext: { quads: [[0.1, 0.2, 0.9, 0.2, 0.9, 0.3, 0.1, 0.3]] }, page: 3,
                      rect: [0.1, 0.2, 0.8, 0.1], region: "loc:0002", title: "Definition 2.1" })],
    lean({ decl: "Notes.Set.inter_sub", ext: { destination: { href: "https://notes.example.invalid/api/Notes/Set.html",
                                                              resolution: "exact" } },
           module: "Notes.Set", role: "support" }),
    { ext: { coverage: "support_only", locator: { physical: [3], printed: [1] } },
      extra: { kind: "definition", note: "The companion states the neighbouring box lemma, not this definition.",
               status: "not_formalized" },
      item: "item:definition-2.1" },
  );
  const unlinked = {
    extensions: { [NAMESPACE]: { clusters: ["cluster:sets"] } },
    id: "item:exercise-3.1", kind: "exercise", object: [],
    subject: [region(sha256, { page: 5, rect: [0.1, 0.1, 0.4, 0.1], region: "loc:0003", title: "Exercise 3.1" })],
  };
  const remark = link(
    [region(sha256, { ...SHARED, title: "A remark on unions" })],
    lean({ decl: "Notes.Internal.aux", module: "Notes.Internal", role: "key",
           ext: { declaration: { id: "private:Notes.Internal:Notes.Internal.aux", kind: "lemma", private: true },
                  destination: { resolution: "exact" } } }),
    { extra: { kind: "passage", status: "formalized" }, item: "item:remark-4.1" },
  );
  // One region, two items: the curation names the remark first, and the
  // equation split off its label (no card) comes first in file order.
  const pair = { region_items: ["item:remark-7.1", "item:equation-7"] };
  const seven = { ext: pair, page: 7, rect: [0.1, 0.5, 0.8, 0.1], region: "loc:0005" };
  const equation = {
    extensions: { [NAMESPACE]: {} },
    id: "item:equation-7", kind: "equation", object: [],
    subject: [region(sha256, { ...seven, title: "Equation 7" })],
  };
  const remarkSeven = link(
    [region(sha256, { ...seven, title: "Remark 7.1 and equation (7)" })],
    lean({ decl: "Notes.Box.volume_nonneg", ext: { destination: { resolution: "exact" } }, role: "key" }),
    { ext: { provenance: CURATION }, extra: { kind: "remark", status: "formalized" }, item: "item:remark-7.1" },
  );
  // A record from elsewhere: no overlay extension on the record or its object end.
  const plain = {
    id: "plain-link", object: [{ type: "html", uri: "../../Notes/Plain/index.html" }],
    subject: [region(sha256, { page: 6, rect: [0.2, 0.2, 0.2, 0.2], title: "Lemma 6.1" })],
  };
  // The key link again, with only the shared region: a producer may split a
  // subject, and the item still has one card per link.
  const [, key] = theoremLinks(sha256);
  const split = { ...key, subject: [key.subject[1]] };
  // A record of another PDF: not this overlay's business.
  const other = { ...unlinked, id: "elsewhere",
                  subject: [region(fakeSha256("e"), { page: 5, rect: [0.1, 0.1, 0.4, 0.1], region: "loc:0003",
                                                      source: { id: OTHER_SOURCE_ID }, title: "Elsewhere" })] };
  return {
    1: [...theoremLinks(sha256), definition, other],
    2: [unlinked, remark, split, plain, equation, remarkSeven],
  };
}

/** A Lean end of a proof step: `lines` of `decl`, with `text` as its snippet when given. */
function stepEnd(decl, lines, text, { ext = {}, truncated = false } = {}) {
  return lean({
    decl,
    ext: { destination: { resolution: "exact" },
           ...(text ? { snippet: { line: lines[0], text, ...(truncated ? { truncated } : {}) } } : {}), ...ext },
    location: { declaration: decl, lines, module: "Notes.Box" },
    role: "key",
  });
}

/**
 * A proof in an item folder: the folder `9/theorem-9.1` holds the theorem and
 * its four steps, two on page 9 and two on page 10, whose ids spell nothing a
 * viewer could parse. Page 9 also holds an item with no proof, and page 11 a
 * checked absence that is not a step.
 */
export function makeProofSections(sha256) {
  const theorem = link(
    [region(sha256, { page: 9, rect: [0.1, 0.1, 0.8, 0.1], title: "Theorem 9.1 (Doubling)" })],
    lean({ decl: "Notes.Box.double", ext: { destination: { resolution: "exact" },
                                            snippet: { line: 18, text: "theorem double (b : Box) : 2 * b = b + b" } },
           role: "key" }),
    { extra: { kind: "theorem", status: "formalized" }, item: "thm" },
  );
  const step = (id, page, y, title, relation, object, extra = {}) => ({
    extensions: { [NAMESPACE]: { provenance: { confirmed_by_human: false, method: "audit" } } },
    id,
    kind: "proof_step",
    object,
    relation,
    status: relation === "noMatch" ? "not_formalized" : relation === "relatedMatch" ? "partial" : "formalized",
    subject: [region(sha256, { page, rect: [0.1, y, 0.8, 0.05], title: `${id}: ${title}` })],
    ...extra,
  });
  const steps = [
    step("a", 9, 0.3, "Unfold the product", "exactMatch", [stepEnd("Notes.Box.double", [20, 21], "intro b\nsimp")]),
    step("b", 9, 0.4, "Collect the two terms", "closeMatch", [
      stepEnd("Notes.Box.double", [22, 23], "rw [two_mul]\nring"),
      stepEnd("Notes.Box.double", [25, 25], "exact h"),
      stepEnd("Notes.Box.double", [27, 40], "calc _ = _ := by\n  simp", { truncated: true }),
    ], { note: "`ring` closes what the book does by hand." }),
    step("c", 10, 0.1, "Bound each side", "relatedMatch", [
      stepEnd("Notes.Box.double", [41, 41], "have := h"),
      stepEnd("Notes.Box.bound", [7, 7], "theorem bound", {
        ext: { declaration: { id: "Notes.Box.bound", kind: "theorem", sorry: "exercise" } },
      }),
    ]),
    step("d", 10, 0.2, "The sum is finite", "noMatch", [
      { location: { declaration: "Notes.Box.double", module: "Notes.Box" }, type: "lean", uri: REPOSITORY },
      { location: { lines: [50, 52], module: "Notes.Set" }, type: "lean", uri: REPOSITORY },
    ], { note: "Lean takes finiteness from an instance." }),
  ];
  const plain = link(
    [region(sha256, { page: 9, rect: [0.1, 0.8, 0.8, 0.1], title: "Remark 9.2" })],
    lean({ decl: "Notes.Box.remark", ext: { destination: { resolution: "exact" } }, role: "key" }),
    { extra: { kind: "remark", status: "formalized" }, item: "remark" },
  );
  const absent = {
    id: "item:gap", kind: "example", object: [{ location: { module: "Notes.Box" }, type: "lean", uri: REPOSITORY }],
    relation: "noMatch", status: "not_formalized",
    subject: [region(sha256, { page: 11, rect: [0.1, 0.1, 0.8, 0.1], title: "Example 11.1" })],
  };
  return { 9: [plain], "9/theorem-9.1": [theorem, ...steps], 11: [absent] };
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
  return annotationFiles(sections).map(({ annotation, path }) => ({ folder: itemFolder(path, "annotations"), path,
                                                                   records: [annotation], url: `${ORIGIN}/${path}` }));
}

// --- verso.json ---------------------------------------------------------------

/** The key card's target, as `tools/verso_extract.py` files its statement. */
export const VERSO_TARGET = "Notes/Box/index.html#Notes___Box___volume_nonneg";
export const VERSO_URL = "../verso.json";

/**
 * A `verso.json` with the key card's statement as Verso runs: their texts
 * join to its snippet, `b` is bound twice, `Box` links a page of the site and
 * `volume` an address elsewhere, which no token may link.
 */
export function makeVerso() {
  return {
    hovers: {
      1: { doc: "The volume of a box is never negative.", signature: "Notes.Box.volume_nonneg (b : Box) : 0 <= b.volume" },
      2: { signature: "Box" },
      3: { doc: "A box: a product of `n` intervals.\n\nSee `Box.volume`.", signature: "Notes.Box : Type" },
      4: { signature: "Notes.Box.volume (b : Box) : ℝ" },
    },
    schema: "formal-companion/overlay-verso",
    schema_version: 1,
    statements: {
      [VERSO_TARGET]: [[
        ["kw", "theorem"], ["tx", " "], ["const", "volume_nonneg", 1], ["tx", " "], ["tx", "(", null, 2],
        ["var", "b", 2, 1], ["tx", " : "], ["const", "Box", 3, null, "Notes/Box/index.html#Notes___Box"],
        ["tx", ")", null, 2], ["tx", " : 0 <= "], ["var", "b", 2, 1], ["tx", "."],
        ["const", "volume", 4, null, "https://elsewhere.invalid/Notes/Box.html#volume"],
      ]],
    },
  };
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

/** A style that takes both `style.left = x` and `style.setProperty("--x", y)`, and reports what it was given. */
export function fakeStyle() {
  return {
    setProperty(name, value) {
      this[name] = String(value);
    },
    getPropertyValue(name) {
      return this[name] ?? "";
    },
  };
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
    style: fakeStyle(),
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
    removeAttribute(name) {
      delete this.attributes[name];
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
    contains: (name) => classes().includes(name),
    remove(...names) {
      element.className = classes().filter((name) => !names.includes(name)).join(" ");
    },
    toggle(name, force) {
      const on = force === undefined ? !classes().includes(name) : Boolean(force);
      if (on) {
        element.classList.add(name);
      } else {
        element.classList.remove(name);
      }
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
    scale,
    userUnit: 1,
    width: 600 * scale,
  };
}

/** The two text items the fake page yields, one span each. */
export const PAGE_TEXT = ["Theorem 1.1 (Volume bound). ", "Every box has a volume."];

/**
 * A PDF.js stand-in. Its `TextLayer` records what the viewer asked it for and
 * appends one span per item of `PAGE_TEXT`; `textLayer: false` leaves it out,
 * as a build without one would, and `textLayerError` makes its render fail.
 */
export function fakePdfjs({ numPages = 12, textLayer = true, textLayerError = null } = {}) {
  const streams = [];
  const page = {
    getViewport: (options) => fakeViewport(options),
    render: () => ({ cancel() {}, promise: Promise.resolve() }),
    rotate: 0,
    streamTextContent: (options) => {
      streams.push(options);
      return { items: PAGE_TEXT };
    },
    view: [0, 0, 600, 800],
  };
  const calls = [];
  const textLayers = [];
  class FakeTextLayer {
    constructor(options) {
      this.cancelled = 0;
      this.options = options;
      this.rendered = false;
      textLayers.push(this);
    }

    async render() {
      if (textLayerError) {
        throw new Error(textLayerError);
      }
      this.options.container.append(...this.options.textContentSource.items.map((text) => {
        const span = fakeElement("span");
        span.textContent = text;
        return span;
      }));
      this.rendered = true;
    }

    cancel() {
      this.cancelled += 1;
    }
  }
  return {
    calls,
    streams,
    textLayers,
    GlobalWorkerOptions: {},
    ...(textLayer ? { TextLayer: FakeTextLayer } : {}),
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
