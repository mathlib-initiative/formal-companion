/**
 * Synthetic fixtures for the overlay viewer's node tests.
 *
 * Everything here is invented: a two-source annotation document about
 * "Elementary Sets: Lecture Notes (synthetic)", a matching
 * `overlay.config.json`, a JSON Schema for a format nobody has published, and
 * a DOM stand-in good enough for the controller. No file, hash, namespace or
 * page count of any real book or companion appears.
 *
 * The format is synthetic on purpose. The viewer takes the accepted `schema`
 * and `schema_version` from the schema file a generated directory ships, so a
 * suite that fixed the real identity would pass whether or not the viewer
 * actually reads that file. These fixtures use `example/annotations` and a
 * major no release has ever carried; every assertion about a refusal is then
 * an assertion that the schema file, not a constant, decided it.
 *
 * The document is otherwise written the way the format wants one: every record
 * array is sorted by id and every set field is sorted. (The node suite does not
 * run the Python checker; keep the order when you add a record, or the fixture
 * stops being an example of a real document.)
 *
 * This file is not a test file (no `.test.mjs` suffix), so `node --test` on this
 * directory imports it but never runs it on its own.
 */
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { documentFormat } from "../runtime/viewer/viewer-core.js";

export const HERE = path.dirname(fileURLToPath(import.meta.url));
export const VIEWER_DIR = path.resolve(HERE, "../runtime/viewer");

/** The identity and version grammar of the synthetic format these fixtures use. */
export const SCHEMA_ID = "example/annotations";
export const SCHEMA_VERSION = "7.2";
export const SCHEMA_VERSION_PATTERN = "^7\\.(0|[1-9][0-9]*)$";

/** A JSON Schema shaped like the format's: it states the identity, and little else. */
export function makeFormatSchema(overrides = {}) {
  return {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    properties: {
      schema: { const: SCHEMA_ID },
      schema_version: { pattern: SCHEMA_VERSION_PATTERN, type: "string" },
    },
    type: "object",
    ...overrides,
  };
}

/** What `documentFormat` reads out of `makeFormatSchema()`. */
export function makeFormat(overrides = {}) {
  return documentFormat(makeFormatSchema(overrides));
}

export const SOURCE_ID = "src:notes";
export const OTHER_SOURCE_ID = "src:site";
export const PDF_ROUTE = "/__overlay_pdf__/notes.pdf";
export const PDF_BYTES = new TextEncoder().encode(
  "synthetic stand-in for a source PDF; not a PDF and not any book's bytes",
);

/** A 64-hex-digit stand-in that is nobody's real digest. */
export function fakeSha256(seed) {
  return seed.repeat(64).slice(0, 64);
}

export async function shellHtml() {
  return readFile(path.join(VIEWER_DIR, "index.html"), "utf8");
}

// --- the annotation document ------------------------------------------------

function pdfSourceRecord(sha256) {
  return {
    bytes: PDF_BYTES.byteLength,
    id: SOURCE_ID,
    kind: "pdf",
    page_size: { height_pt: 800, width_pt: 600 },
    // Page 5 is a fold-out: a smaller box than the rest of the run.
    page_sizes: [{ first: 5, height_pt: 400, last: 5, width_pt: 300 }],
    physical_page_count: 12,
    printed_page_offset: 4,
    role: "audited",
    sha256,
    title: "Elementary Sets: Lecture Notes (synthetic)",
    url: "https://notes.example.invalid/elementary-sets.pdf",
  };
}

function region(page, extra) {
  return {
    coordinate_space: {
      origin: "top_left",
      page_box: "CropBox",
      units: "normalized",
      x_axis: "right",
      y_axis: "down",
    },
    page,
    type: "PdfRegionSelector",
    ...extra,
  };
}

function location(id, items, selector, pages) {
  return {
    id,
    items,
    pages,
    provenance: "prov:layout",
    target: { selector: [selector], source: SOURCE_ID },
  };
}

function declaration(id, extra = {}) {
  return {
    anonymous: false,
    file: "Notes/Box.lean",
    id,
    id_source: "name",
    kind: "theorem",
    module: "Notes.Box",
    name: id,
    private: false,
    ...extra,
  };
}

function link(id, item, declarationName, role, extra = {}) {
  return { declaration: declarationName, id, item, provenance: "prov:curation", role, ...extra };
}

/**
 * The document every test starts from. `overrides` is merged at the top level,
 * so a test can replace one record array wholesale.
 */
export function makeDocument(sha256, overrides = {}) {
  return {
    changelog: [
      {
        at: "2026-01-04",
        event: "retired",
        id: "chg:0001",
        reason: "removed",
        subject: "Notes.Box.volume_retired",
        subject_kind: "declaration",
      },
    ],
    clusters: [
      {
        id: "cluster:sets",
        items: ["item:exercise-3.1"],
        locator: "Section 3",
        title: "Elementary sets",
      },
    ],
    companion: {
      commit: "0".repeat(40),
      generated_at: "2026-02-01T00:00:00Z",
      generator: "hand",
      name: "notes-companion",
      repo: "git.example.invalid/an-owner/notes-companion",
    },
    declarations: [
      declaration("Notes.Box.volume_mono"),
      declaration("Notes.Box.volume_nonneg"),
      declaration("Notes.Set.inter_sub", { file: "Notes/Set.lean", module: "Notes.Set" }),
      declaration("Notes.Set.union_comm", {
        file: "Notes/Set.lean",
        module: "Notes.Set",
        sorry: "exercise",
      }),
      {
        anonymous: true,
        file: "Notes/Set.lean",
        id: "example:0123456789ab",
        id_source: "statement_hash",
        kind: "example",
        module: "Notes.Set",
        private: false,
        statement_sha256: "0123456789ab".padEnd(64, "0"),
      },
      {
        anonymous: false,
        file: "Notes/Internal.lean",
        id: "private:Notes.Internal:Notes.Internal.aux",
        id_source: "private",
        kind: "lemma",
        module: "Notes.Internal",
        name: "Notes.Internal.aux",
        private: true,
      },
    ],
    destinations: [
      {
        declaration: "Notes.Box.volume_mono",
        fallback_label: "module page (fallback)",
        href: "Notes/Box/index.html",
        id: "dest:0002",
        resolution: "fallback",
        site: "verso_literate",
      },
      {
        declaration: "Notes.Box.volume_nonneg",
        fragment: "Notes___Box___volume_nonneg",
        href: "Notes/Box/index.html",
        id: "dest:0003",
        resolution: "exact",
        site: "verso_literate",
      },
      {
        declaration: "Notes.Set.inter_sub",
        fragment: "Notes___Set___inter_sub",
        href: "https://notes.example.invalid/api/Notes/Set.html",
        id: "dest:0004",
        resolution: "exact",
        site: "doc_gen4",
      },
    ],
    items: [
      {
        coverage: "formalized",
        form: "numbered",
        id: "exercise-3.1",
        id_source: "label",
        kind: "Exercise",
        number: "3.1",
        source: OTHER_SOURCE_ID,
      },
      {
        coverage: "support_only",
        form: "numbered",
        id: "item:definition-2.1",
        id_source: "declared",
        kind: "Definition",
        number: "2.1",
        pages: { basis: "physical", first: 3, last: 4 },
        section: "Section 2",
        source: SOURCE_ID,
        x_coverage_note: "The companion states the neighbouring box lemma, not this definition.",
      },
      {
        cluster_fallback: true,
        clusters: ["cluster:sets"],
        form: "numbered",
        id: "item:exercise-3.1",
        id_source: "declared",
        kind: "Exercise",
        number: "3.1",
        pages: { basis: "physical", first: 5 },
        section: "Section 3",
        source: SOURCE_ID,
      },
      {
        form: "passage",
        id: "item:remark-4.1",
        id_source: "declared",
        kind: "passage",
        pages: { basis: "physical", first: 2 },
        source: SOURCE_ID,
        title: "A remark on unions",
      },
      {
        form: "numbered",
        id: "item:theorem-1.1",
        id_source: "declared",
        kind: "Theorem",
        label: "Theorem 1.1",
        number: "1.1",
        pages: { basis: "physical", first: 2 },
        section: "Section 1",
        source: SOURCE_ID,
      },
    ],
    links: [
      // Deliberately not in role order: the viewer is what orders them.
      link("link:0001", "item:theorem-1.1", "Notes.Set.union_comm", "helper"),
      link("link:0002", "item:theorem-1.1", "Notes.Box.volume_nonneg", "key", {
        relation: "exact",
      }),
      link("link:0003", "item:theorem-1.1", "example:0123456789ab", "example"),
      link("link:0004", "item:theorem-1.1", "Notes.Set.inter_sub", "support"),
      link("link:0005", "item:theorem-1.1", "Notes.Box.volume_mono", "attributed"),
      link("link:0006", "item:definition-2.1", "Notes.Set.inter_sub", "support"),
      // A link whose declaration the changelog retired: never shown.
      link("link:0007", "item:definition-2.1", "Notes.Box.volume_retired", "support", {
        orphaned: true,
      }),
      link("link:0008", "item:remark-4.1", "private:Notes.Internal:Notes.Internal.aux", "key"),
      // A link whose target is a cluster rather than one declaration.
      {
        cluster: "cluster:sets",
        id: "link:0009",
        item: "item:remark-4.1",
        provenance: "prov:curation",
        role: "support",
      },
    ],
    locations: [
      location("loc:0001", ["item:theorem-1.1"], region(2, { rect: { x0: 0.1, x1: 0.8, y0: 0.1, y1: 0.2 } }),
               { basis: "physical", first: 2 }),
      // A quad region with the origin at the bottom left.
      location("loc:0002", ["item:definition-2.1"], {
        coordinate_space: {
          origin: "bottom_left",
          page_box: "CropBox",
          units: "normalized",
          x_axis: "right",
          y_axis: "up",
        },
        page: 3,
        quads: [{ x0: 0.1, x1: 0.9, x2: 0.9, x3: 0.1, y0: 0.8, y1: 0.8, y2: 0.7, y3: 0.7 }],
        type: "PdfRegionSelector",
      }, { basis: "physical", first: 3 }),
      // A pt region on the fold-out page, reached through a page= fragment.
      location("loc:0003", ["item:exercise-3.1"], {
        conformsTo: "http://tools.ietf.org/rfc/rfc8118",
        refinedBy: {
          coordinate_space: {
            origin: "top_left",
            page_box: "CropBox",
            units: "pt",
            x_axis: "right",
            y_axis: "down",
          },
          page: 5,
          rect: { x0: 30, x1: 150, y0: 40, y1: 80 },
          type: "PdfRegionSelector",
        },
        type: "FragmentSelector",
        value: "page=5",
      }, { basis: "physical", first: 5 }),
      // One region, two items.
      location("loc:0004", ["item:remark-4.1", "item:theorem-1.1"],
               region(2, { rect: { x0: 0.1, x1: 0.8, y0: 0.4, y1: 0.5 } }),
               { basis: "physical", first: 2 }),
      // A location on the other source: not this overlay's business.
      {
        id: "loc:0005",
        items: ["exercise-3.1"],
        target: {
          selector: [{ path: "notes/index.html", type: "SitePageSelector" }],
          source: OTHER_SOURCE_ID,
        },
      },
    ],
    memberships: [
      {
        cluster: "cluster:sets",
        declaration: "Notes.Set.union_comm",
        id: "mem:0001",
        provenance: "prov:curation",
        role: "consumer",
      },
      {
        cluster: "cluster:sets",
        declaration: "Notes.Set.inter_sub",
        id: "mem:0002",
        provenance: "prov:curation",
        role: "primary",
      },
    ],
    provenance: [
      {
        actor: "human",
        at: "2026-01-02",
        confirmed_by_human: true,
        id: "prov:curation",
        method: "curation",
      },
      {
        actor: "tool",
        at: "2026-01-02",
        confirmed_by_human: false,
        id: "prov:layout",
        method: "layout_extraction",
      },
    ],
    schema: SCHEMA_ID,
    schema_version: SCHEMA_VERSION,
    snippets: [
      {
        declaration: "Notes.Box.volume_nonneg",
        id: "snip:0001",
        line: 12,
        text: "theorem volume_nonneg (b : Box) : 0 <= b.volume",
      },
    ],
    sources: [
      pdfSourceRecord(sha256),
      {
        id: OTHER_SOURCE_ID,
        kind: "web",
        revision: "2026-01-01",
        scope: "site",
        title: "The companion's rendered notes (synthetic)",
      },
    ],
    verdicts: [
      {
        declarations: [
          { declaration: "Notes.Box.volume_mono", note: "left unchanged", verdict: "deviation" },
          { declaration: "Notes.Box.volume_nonneg", note: "repaired in place", verdict: "faithful" },
        ],
        evidence: ["Notes/Box.lean:12", "source page 2"],
        id: "verdict:0001",
        item: "item:theorem-1.1",
        resolution: { pr: 7, state: "fixed" },
        severity: "medium",
        status: "fixed",
        summary: "The box bound was stated for closed boxes only.",
        verdict: "deviation",
      },
      {
        declarations: [{ declaration: "Notes.Set.inter_sub", verdict: "proxy" }],
        evidence: ["Notes/Set.lean:40"],
        id: "verdict:0002",
        item: "item:definition-2.1",
        severity: "low",
        status: "unconfirmed",
        summary: "Stated through the library's intersection, not the source's.",
        verdict: "proxy",
      },
    ],
    ...overrides,
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
  element.classList = {
    add(...names) {
      const have = String(element.className).split(" ").filter(Boolean);
      element.className = [...new Set([...have, ...names])].join(" ");
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
  const bytes = value instanceof Uint8Array ? value : new TextEncoder().encode(JSON.stringify(value));
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

export function fakePdfjs({ numPages = 12 } = {}) {
  const page = {
    getViewport: () => ({ convertToViewportPoint: (x, y) => [x, 800 - y], height: 800, width: 600 }),
    render: () => ({ cancel() {}, promise: Promise.resolve() }),
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
