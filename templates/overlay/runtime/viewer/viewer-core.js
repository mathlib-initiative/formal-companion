/**
 * The overlay viewer's pure functions: the annotation bundle, the Literature
 * Links 1.0 records, coordinate conversion, the href rules and sha256
 * verification.
 *
 * Nothing here touches the DOM, so `node --test` exercises all of it; viewer.js
 * holds the controller. Nothing here is specific to one companion either: every
 * per-book value arrives as data, from `overlay.config.json`
 * (docs/overlay.md section 3), from `GENERATED.json`'s annotations block and
 * from the bundle, and a value this module cannot read is an error or a
 * warning with a message, never a guess.
 */

// --- the files --------------------------------------------------------------

export const CONFIG_SCHEMA = "formal-companion/overlay-config";
export const CONFIG_SCHEMA_VERSION = 1;
export const CONFIG_URL = "../overlay.config.json";
/** `GENERATED.json`: its `annotations` block names the bundle (docs/overlay.md section 2). */
export const GENERATED_URL = "../GENERATED.json";
/** The extension key the overlay's records carry its own facts under. */
export const NAMESPACE = "org.mathlib-initiative.formal-companion.overlay";

/** The viewer never reads a JSON file larger than this. */
export const MAX_JSON_BYTES = 64 * 1024 * 1024;

/** `pdf.route` must start with this and carry no `..` (the contract's section 3). */
export const ROUTE_PREFIX = "/__";
/**
 * `pdf.publish.license`: the open licenses under which the PDF may be published
 * with the site (the contract's section 3). A published PDF sits beside
 * `overlay.config.json`, so its URL is relative and works under a project path.
 */
export const OPEN_LICENSES = Object.freeze(["Apache-2.0", "CC-BY-4.0", "CC-BY-SA-4.0", "CC0-1.0", "MIT"]);
const PUBLISHED_FILE_NAME = /^[A-Za-z0-9_][A-Za-z0-9._-]*\.pdf$/;

export const DEFAULT_MOUNT = "pdf-overlay";
export const DEFAULT_STORAGE_KEY = "formal-companion-overlay-page";

/** How many declaration cards one page of the card list holds. */
export const CARD_PAGE_SIZE = 25;

// --- vocabulary -------------------------------------------------------------

/** A link's role, in the order the card list shows them (the contract's section 4). */
export const LINK_ROLES = Object.freeze(["key", "attributed", "support", "example", "helper"]);

/** A verdict's `verdict`, and each declaration's. */
export const VERDICT_KINDS = Object.freeze([
  "faithful",
  "convention",
  "attribution_gap",
  "proxy",
  "deviation",
]);
/** The label each verdict kind shows; viewer.css has a colour for each one. */
export const VERDICT_LABELS = Object.freeze({
  attribution_gap: "attribution gap",
  convention: "convention delta",
  deviation: "statement deviation",
  faithful: "faithful",
  proxy: "proxy definition",
});

/**
 * One generic line per status: what the item's links do NOT show on their
 * own. `formalized`, `partial` and `not_formalized` are Literature Links'
 * recommended values; the others are curation classes, which the exporter
 * keeps in the record extension's `coverage`. No value is ever guessed: an
 * unknown one shows no summary.
 */
export const COVERAGE_SUMMARIES = Object.freeze({
  example_only: "Formalized only by anonymous examples; no named declaration is attributed to it.",
  formalized: "Formalized: a key declaration states it.",
  not_formalized: "Not formalized.",
  out_of_scope: "Outside the stated scope of the formalization.",
  partial: "Formalized in part or in a different form; see the faithfulness verdict, if any.",
  private_only: "Stated only by a private declaration.",
  support_only: "Supported only by declarations that state a neighboring item.",
});

/** Every status `COVERAGE_SUMMARIES` has a line for, sorted. */
export const COVERAGE_VALUES = Object.freeze(Object.keys(COVERAGE_SUMMARIES).sort());

/** A record of this `kind` is one step of its item's proof (docs/overlay.md section 4). */
export const STEP_KIND = "proof_step";

/** The word a step's relation prints; a token not listed prints as written. */
export const RELATION_WORDS = Object.freeze({
  broadMatch: "Broader",
  closeMatch: "Close match",
  exactMatch: "Exact match",
  hasPart: "In part",
  narrowMatch: "Narrower",
  noMatch: "No match",
  relatedMatch: "Related",
});

/** What a step's relation claims about its Lean lines: the word's title. */
export const RELATION_SUMMARIES = Object.freeze({
  broadMatch: "The Lean lines prove more than this step.",
  closeMatch: "The Lean lines reach the same claim by other means.",
  exactMatch: "The Lean lines make this step's move.",
  hasPart: "The Lean lines carry out part of this step.",
  narrowMatch: "The Lean lines prove a special case of this step.",
  noMatch: "Checked: the Lean has no counterpart to this step.",
  relatedMatch: "The step's content enters the Lean at these lines, which do not make its move.",
});

// --- small helpers ----------------------------------------------------------

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function text(value) {
  return typeof value === "string" && value.trim() ? value.trim() : "";
}

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function fail(message) {
  throw new Error(message);
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

/** The overlay's own extension object of a record or an end, or {}. */
export function extensionOf(node) {
  const value = isObject(node) && isObject(node.extensions) ? node.extensions[NAMESPACE] : null;
  return isObject(value) ? value : {};
}

// --- hashing ----------------------------------------------------------------

export function bytesToHex(bytes) {
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function sha256Hex(bytes, cryptoProvider = globalThis.crypto) {
  if (!cryptoProvider?.subtle) {
    throw new Error("WebCrypto is unavailable; the overlay data cannot be verified safely.");
  }
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const digest = await cryptoProvider.subtle.digest(
    "SHA-256",
    view.buffer.slice(view.byteOffset, view.byteOffset + view.byteLength),
  );
  return bytesToHex(new Uint8Array(digest));
}

/**
 * Compare the bytes with the sha256 the records' PDF ends state.
 * The expected value is a parameter: no hash of any one PDF lives in a template.
 */
export async function verifyPdfBytes(bytes, expected, cryptoProvider = globalThis.crypto) {
  const want = text(expected).toLowerCase();
  if (!/^[a-f\d]{64}$/.test(want)) {
    throw new Error("The source's sha256 is not 64 hex digits; the PDF cannot be verified.");
  }
  const actual = await sha256Hex(bytes, cryptoProvider);
  return { actual, expected: want, ok: actual === want };
}

// --- hrefs ------------------------------------------------------------------

const SCHEME = /^[a-z][a-z\d+.-]*:/i;

/** An `https:` URL with no fragment of its own, or null. */
export function safeHttpsUrl(value) {
  const url = typeof value === "string" ? value : "";
  return /^https:\/\/[^\s#\\]+$/.test(url) ? url : null;
}

/**
 * The back link's own `href`, or null. It is the one href the config supplies,
 * and it usually climbs out of the mount (`../../index.html`): a relative path
 * is accepted with its `..` segments and an `https:` URL whole, but a
 * scheme-bearing (`javascript:`, `data:`), protocol-relative or root-absolute
 * value is refused so the header can never carry a live link the config
 * smuggled in.
 */
export function safeBackLinkHref(value) {
  const href = text(value);
  if (!href) {
    return null;
  }
  if (safeHttpsUrl(href)) {
    return href;
  }
  if (/[\s#]/.test(href) || href.includes("\\") || href.startsWith("/") || SCHEME.test(href)) {
    return null;
  }
  return href;
}

/**
 * Where a reference leads, or null: `uri` resolved against `baseUrl`, used
 * only when it is an `https:` URL or an `http(s):` URL of the page's own
 * origin: a `javascript:`, `data:` or foreign plain-`http:` target is never a
 * link. An end's own `uri` resolves against the file that holds the
 * annotation (the specification's "References"),
 * `<site root>/<directory>/<section>/[<item>/]<name>.json`; a rendered page
 * resolves against the site root.
 */
export function targetHref(target, baseUrl, pageOrigin) {
  const uri = typeof target === "string" ? target : isObject(target) ? target.uri : null;
  if (typeof uri !== "string" || !uri || /[\s\\]/.test(uri)) {
    return null;
  }
  let resolved;
  try {
    resolved = new URL(uri, baseUrl);
  } catch {
    return null;
  }
  if (resolved.username || resolved.password) {
    return null;
  }
  if (resolved.protocol === "https:") {
    return resolved.href;
  }
  if (resolved.protocol === "http:" && resolved.origin === pageOrigin) {
    return resolved.href;
  }
  return null;
}

/**
 * A page of this site that `href` (relative to `siteUrl`) names, or null: a
 * token links nowhere else. The page's origin is not enough, since a project
 * site shares it with its owner's other sites: the page must be under `siteUrl`.
 */
export function siteHref(href, siteUrl, pageOrigin) {
  const resolved = targetHref(href, siteUrl, pageOrigin);
  if (!resolved) {
    return null;
  }
  const url = new URL(resolved);
  return url.origin === pageOrigin && url.pathname.startsWith(new URL("./", siteUrl).pathname) ? resolved : null;
}

/** A Lean module's rendered page relative to the site root: `A/B/C/index.html` for `A.B.C`. */
export function modulePage(module) {
  const name = text(module);
  return name ? `${name.split(".").join("/")}/index.html` : null;
}

/** The anchor a rendered page gives a declaration: its name with each "." as "___". */
export function versoFragment(declaration) {
  const name = text(declaration);
  return name ? name.split(".").join("___") : null;
}

/**
 * The rendered page an overlay card links, or null. The end's `destination`
 * extension says there is one: its page is the module's
 * (`<site root>/<module as a path>/index.html`) and an `exact` one adds the
 * declaration's anchor, unless the destination names its own `href`
 * (relative to the site root, or an `https:` URL) or `fragment`. `siteUrl`
 * is the site root, where the rendered pages and the annotation directory are.
 */
export function cardHref(end, siteUrl, pageOrigin) {
  const destination = cardDestination(end);
  return destination ? targetHref(destination.fragment
    ? `${destination.page}#${encodeURIComponent(destination.fragment)}` : destination.page, siteUrl, pageOrigin)
    : null;
}

/** `{page, fragment}` of a card's rendered page, before resolution, or null; "" for no fragment. */
function cardDestination(end) {
  const destination = isObject(extensionOf(end).destination) ? extensionOf(end).destination : null;
  const location = isObject(end?.location) ? end.location : {};
  const page = !destination ? null
    : typeof destination.href === "string" ? destination.href : modulePage(location.module);
  if (!page) {
    return null;
  }
  const fragment = destination.resolution !== "exact" ? ""
    : typeof destination.fragment === "string" ? destination.fragment : versoFragment(location.declaration) ?? "";
  return { fragment, page };
}

/** An example's declared id when it is its tag: the tag comment holds it in backticks. */
const EXAMPLE_ID = /^example:[A-Za-z0-9][A-Za-z0-9._-]*$/;

/**
 * `<page>#<fragment>` of a card's rendered page when it has an anchor, or
 * `<page>#<tag>` of a tagged example on its module page (an example has no
 * anchor), or null: the key `verso.json` files the card's statement under.
 * The page is as `cardHref` reads it, relative to the site root;
 * `tools/verso_extract.py` builds the same key.
 */
export function cardTarget(end) {
  const destination = cardDestination(end);
  const declaration = isObject(extensionOf(end).declaration) ? extensionOf(end).declaration : {};
  if (!destination) {
    return null;
  }
  if (destination.fragment) {
    return `${destination.page}#${destination.fragment}`;
  }
  const exact = extensionOf(end).destination.resolution === "exact";
  return !exact && declaration.kind === "example" && typeof declaration.id === "string" && EXAMPLE_ID.test(declaration.id)
    ? `${destination.page}#${declaration.id}` : null;
}

// --- the configuration ------------------------------------------------------

/** `../` once per directory between `<mount>/viewer/` and the site root. */
export function siteRootFor(mount) {
  const parts = text(mount).split("/").filter(Boolean);
  if (!parts.length || parts.some((part) => part === "." || part === "..")) {
    fail(`overlay.config.json has site.mount ${JSON.stringify(mount)}; a mount is a relative path.`);
  }
  return "../".repeat(parts.length + 1);
}

/**
 * Read `overlay.config.json`: check what the viewer relies on, and fill in the
 * defaults of docs/overlay.md section 3 for everything else. The full rule set
 * is `tools/check_generated.py`'s; this is the subset the page needs before it
 * will fetch anything.
 */
export function readConfig(raw) {
  if (!isObject(raw)) {
    fail("overlay.config.json must be a JSON object.");
  }
  if (raw.schema !== CONFIG_SCHEMA) {
    fail(`overlay.config.json has schema ${JSON.stringify(raw.schema)}; expected "${CONFIG_SCHEMA}".`);
  }
  if (raw.schema_version !== CONFIG_SCHEMA_VERSION) {
    fail(`overlay.config.json has schema_version ${JSON.stringify(raw.schema_version)}; `
         + `expected ${CONFIG_SCHEMA_VERSION}.`);
  }
  const sourceId = text(raw.source_id);
  if (!sourceId) {
    fail("overlay.config.json names no source_id.");
  }
  const title = text(raw.title);
  if (!title) {
    fail("overlay.config.json names no title.");
  }
  const { pdfLicense, pdfUrl } = readPdf(isObject(raw.pdf) ? raw.pdf : {});
  // No back_link, or one whose href is refused, is no back link: the top bar starts with the title.
  const back = isObject(raw.back_link) ? raw.back_link : {};
  const backHref = safeBackLinkHref(back.href);
  const backText = text(back.text);
  const display = isObject(raw.display) ? raw.display : {};
  const site = isObject(raw.site) ? raw.site : {};
  return {
    backLink: backHref && backText ? { href: backHref, text: backText } : null,
    lede: text(raw.lede),
    pdfLicense,
    pdfUrl,
    // build_site.py publishes the annotation files at the site root, the same
    // place relative to the built pages as in the repository; it is this far up.
    siteRoot: siteRootFor(site.mount ?? DEFAULT_MOUNT),
    sourceId,
    storageKey: text(raw.storage_key) || DEFAULT_STORAGE_KEY,
    stripPrefix: text(display.strip_prefix),
    minimal: display.minimal === true,
    title,
  };
}

/**
 * Where the PDF is fetched from, relative to the viewer page: `pdf.route` on
 * loopback, or, when `pdf.publish` names an open license, `../<file_name>`,
 * beside `overlay.config.json` in the built site.
 */
function readPdf(pdf) {
  if (pdf.publish === undefined) {
    const route = text(pdf.route);
    if (!route.startsWith(ROUTE_PREFIX) || route.includes("..")) {
      fail(`overlay.config.json has pdf.route ${JSON.stringify(route)}; a route starts with `
           + `"${ROUTE_PREFIX}" and carries no "..".`);
    }
    return { pdfLicense: null, pdfUrl: route };
  }
  const license = isObject(pdf.publish) ? pdf.publish.license : undefined;
  if (!OPEN_LICENSES.includes(license)) {
    fail(`overlay.config.json has pdf.publish.license ${JSON.stringify(license ?? null)}; `
         + `a published PDF carries one of ${OPEN_LICENSES.join(", ")}.`);
  }
  if (typeof pdf.file_name !== "string" || !PUBLISHED_FILE_NAME.test(pdf.file_name)) {
    fail(`overlay.config.json has pdf.file_name ${JSON.stringify(pdf.file_name ?? null)}; `
         + "a published PDF is one plain file name ending in .pdf.");
  }
  return { pdfLicense: license, pdfUrl: `../${pdf.file_name}` };
}

/** The absolute URL the viewer fetches the PDF from, resolved against its own page. */
export function pdfFetchUrl(config, pageUrl) {
  return new URL(config.pdfUrl, pageUrl).href;
}

// --- the bundle -------------------------------------------------------------

export const BUNDLE_SCHEMA = "formal-companion/overlay-annotations";
const SECTION_NAME = /^[0-9A-Za-z][0-9A-Za-z._-]*$/;
const ITEM_NAME = /^[0-9A-Za-z_-][0-9A-Za-z._-]{0,119}$/;
const ANNOTATION_NAME = /^[0-9A-Za-z_-][0-9A-Za-z._-]{0,114}\.json$/;
/** The bundle sits beside `GENERATED.json`. */
const BUNDLE_NAME = /^[0-9A-Za-z][0-9A-Za-z._-]*\.json$/;

function safeRelative(value) {
  return typeof value === "string" && value !== "" && !value.includes("\\")
    && !value.startsWith("/") && value.split("/").every((part) => part && part !== "." && part !== "..");
}

/**
 * `GENERATED.json`'s `annotations` block: the annotation directory, the number
 * of files and the bundle `{path, sha256}`, its path relative to
 * `GENERATED.json`. A browser cannot list a directory; the bundle holds every
 * file, and its sha256 is checked when it arrives.
 */
export function readAnnotationsBlock(generated) {
  const block = isObject(generated) ? generated.annotations : null;
  if (!isObject(block)) {
    fail("GENERATED.json has no annotations block; `cf overlay export` and `cf overlay sync` write it.");
  }
  if (!safeRelative(block.directory)) {
    fail("GENERATED.json names no relative annotations.directory.");
  }
  if (!Number.isInteger(block.count) || block.count < 1) {
    fail("GENERATED.json records no annotation file; `cf overlay export` writes them.");
  }
  const bundle = isObject(block.bundle) ? block.bundle : {};
  if (!BUNDLE_NAME.test(text(bundle.path)) || !/^[a-f\d]{64}$/.test(text(bundle.sha256))) {
    fail("GENERATED.json annotations.bundle is not {path, sha256}; `cf overlay sync` writes it.");
  }
  return { bundle: { path: bundle.path, sha256: bundle.sha256 }, count: block.count, directory: block.directory };
}

/** `<section>/<item>` of an annotation file's path, or null for a file directly in a section. */
export function itemFolder(path, directory) {
  const parts = text(path).slice(directory.length + 1).split("/");
  return parts.length === 3 ? parts.slice(0, 2).join("/") : null;
}

/**
 * The bundle's annotations, `[{path, annotation}]` in file order, folder
 * defaults applied. Each path is `<directory>/<section>/[<item>/]<name>.json`,
 * the file the annotation's relative references resolve against.
 */
export function parseBundle(source, block) {
  let value;
  try {
    value = JSON.parse(source);
  } catch (error) {
    fail(`${block.bundle.path} is not JSON (${error instanceof Error ? error.message : error}).`);
  }
  if (!isObject(value) || value.schema !== BUNDLE_SCHEMA || value.directory !== block.directory
      || !Array.isArray(value.annotations)) {
    fail(`${block.bundle.path} is not a ${BUNDLE_SCHEMA} bundle of ${block.directory}/.`);
  }
  const prefix = `${block.directory}/`;
  const rows = value.annotations.map((row, index) => {
    const path = isObject(row) ? row.path : null;
    const parts = typeof path === "string" && path.startsWith(prefix) ? path.slice(prefix.length).split("/") : [];
    const name = parts[parts.length - 1];
    if (!safeRelative(path) || (parts.length !== 2 && parts.length !== 3) || !SECTION_NAME.test(parts[0])
        || !parts.slice(1, -1).every((part) => ITEM_NAME.test(part)) || !ANNOTATION_NAME.test(name)
        || name === "_defaults.json" || !isObject(row.annotation)) {
      fail(`${block.bundle.path} annotations[${index}] is not {path: <directory>/<section>/[<item>/]<name>.json, annotation}.`);
    }
    return { annotation: row.annotation, path };
  });
  if (rows.length !== block.count || value.count !== block.count) {
    fail(`${block.bundle.path} holds ${rows.length} annotations; GENERATED.json records ${block.count}.`);
  }
  return rows;
}

// --- geometry (Literature Links' PDF location) -------------------------------

const EPSILON = 1e-9;

function fraction(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1;
}

/**
 * `{page, quads}` of a PDF end's location: its `rect`, `[x, y, width,
 * height]` as fractions of the displayed page from the top left, or the
 * extension's quads when the region was drawn as quadrilaterals. A location
 * this viewer cannot place throws; the caller drops the region and says so.
 */
export function endRegion(end) {
  const location = isObject(end) ? end.location : null;
  const page = isObject(location) ? location.page : undefined;
  if (!Number.isInteger(page) || page < 1) {
    fail("A region has no physical page.");
  }
  const rect = location.rect;
  if (!Array.isArray(rect) || rect.length !== 4 || !rect.every(fraction)) {
    fail(`The region on page ${page} has no rect of four page fractions.`);
  }
  const [x, y, width, height] = rect;
  if (!(width > 0 && height > 0) || x + width > 1 + EPSILON || y + height > 1 + EPSILON) {
    fail(`The region on page ${page} has a rect with no area or off the page.`);
  }
  const quads = asArray(extensionOf(end).quads);
  if (quads.length) {
    return {
      page,
      quads: quads.map((quad) => {
        if (!Array.isArray(quad) || quad.length !== 8 || !quad.every(fraction)) {
          fail(`The region on page ${page} has a malformed quad.`);
        }
        return [0, 2, 4, 6].map((n) => [quad[n], quad[n + 1]]);
      }),
    };
  }
  return { page, quads: [[[x, y], [x + width, y], [x + width, y + height], [x, y + height]]] };
}

/**
 * A point in fractions of the displayed page (top-left origin, after the
 * page's own rotation) placed in a PDF.js viewport. `displayed` is the page's
 * viewport at its own rotation and scale 1; its `convertToPdfPoint` takes the
 * point back to PDF space, whatever extra rotation `viewport` carries.
 */
export function pointToViewport(point, displayed, viewport) {
  const [x, y] = Array.isArray(point) ? point.map(Number) : [NaN, NaN];
  if (!Number.isFinite(x) || !Number.isFinite(y) || x < 0 || x > 1 || y < 0 || y > 1) {
    throw new Error("Overlay coordinates must be finite page fractions in [0, 1].");
  }
  if (!(displayed?.width > 0) || !(displayed?.height > 0)
      || typeof displayed.convertToPdfPoint !== "function") {
    throw new Error("The displayed page viewport is invalid.");
  }
  if (typeof viewport?.convertToViewportPoint !== "function") {
    throw new Error("A PDF.js viewport is required for overlay transformation.");
  }
  const [px, py] = displayed.convertToPdfPoint(x * displayed.width, y * displayed.height);
  return viewport.convertToViewportPoint(px, py);
}

export function quadToViewport(quad, displayed, viewport) {
  if (!Array.isArray(quad) || quad.length !== 4) {
    throw new Error("An overlay quadrilateral must contain four points.");
  }
  return quad.map((point) => pointToViewport(point, displayed, viewport));
}

// --- verdict display --------------------------------------------------------

/**
 * The item-level verdict note. A `fixed` entry is history, not a live finding:
 * it keeps its kind in the label so the record stays legible, and drops the
 * kind's warning colour.
 */
export function verdictDisplay(verdict, status) {
  const label = VERDICT_LABELS[verdict] ?? verdict;
  return status === "fixed"
    ? { className: "verdict-fixed", label: `fixed: ${label}` }
    : { className: `verdict-${verdict}`, label };
}

/**
 * The badge on one declaration card. Under a fixed verdict a declaration is
 * either repaired in place (its verdict is then `faithful`) or left as it was,
 * with the fix in another declaration; the card still shows the old one, which
 * still deviates, so it keeps its kind and colour and says it is superseded.
 */
export function declarationVerdictDisplay(verdict, status) {
  const label = VERDICT_LABELS[verdict] ?? verdict;
  if (verdict === "faithful") {
    return { className: "verdict-faithful", label };
  }
  return status === "fixed"
    ? { className: `verdict-${verdict}`, label: `${label}, superseded` }
    : { className: `verdict-${verdict}`, label };
}

/**
 * One verdict line under an item: its kind (with the severity when there is
 * one), the summary, the resolution and the status word when it says more
 * than the label does. "unconfirmed" is never a word on the line: the item
 * footer says once whether the verdicts were reviewed. The title (a tooltip)
 * holds the evidence, the provenance's note and its method.
 */
export function verdictLine(verdict) {
  const display = verdictDisplay(verdict.verdict, verdict.status);
  const severity = text(verdict.severity) && verdict.severity !== "none" ? ` (${verdict.severity})` : "";
  const provenance = isObject(verdict.provenance) ? verdict.provenance : {};
  const status = text(verdict.status);
  // A fixed verdict's label already says "fixed"; its resolution need not say it again.
  const resolution = resolutionText(verdict.resolution);
  return {
    className: display.className,
    label: `${display.label}${severity}`,
    resolution: status === "fixed" ? resolution.replace(/^fixed(, |$)/, "") : resolution,
    statusWord: status && status !== "unconfirmed" && status !== "fixed" ? status.replace(/_/g, " ") : "",
    summary: text(verdict.summary),
    title: [...asArray(verdict.evidence), text(provenance.note), text(provenance.method)]
      .filter((line) => text(line)).join("\n"),
  };
}

/** The one line a verdict's resolution adds, or "" when the verdict is open. */
export function resolutionText(resolution) {
  if (!isObject(resolution) || !text(resolution.state) || resolution.state === "open") {
    return "";
  }
  const parts = [resolution.state.replace(/_/g, " ")];
  if (Number.isInteger(resolution.pr)) {
    parts.push(`PR #${resolution.pr}`);
  }
  if (Number.isInteger(resolution.issue)) {
    parts.push(`issue #${resolution.issue}`);
  }
  if (text(resolution.branch)) {
    parts.push(`branch ${text(resolution.branch)}`);
  }
  if (text(resolution.note)) {
    parts.push(text(resolution.note));
  }
  return parts.join(", ");
}

// --- coverage and summaries -------------------------------------------------

/** The generic one-line summary of a status, or "" for an unknown one. */
export function coverageSummary(coverage) {
  return COVERAGE_SUMMARIES[coverage] ?? "";
}

// --- the index --------------------------------------------------------------

function pageList(pages) {
  const list = asArray(pages).filter(Number.isInteger);
  return list.length ? `${list.length === 1 ? "p." : "pp."} ${list.join(", ")}` : "";
}

/** Where the item is in the source: its locator text, else its printed and physical pages. */
export function locatorText(locator) {
  if (!isObject(locator)) {
    return "";
  }
  if (text(locator.text)) {
    // Curated locators write ranges the LaTeX way: "pp. 46--49".
    return text(locator.text).replace(/(\d)--(?=\d)/g, "$1–");
  }
  const printed = pageList(locator.printed);
  const physical = pageList(locator.physical);
  if (printed && printed === physical) {
    return printed;
  }
  return [printed && `printed ${printed}`, physical && `PDF ${physical}`].filter(Boolean).join(", ");
}

/** The item a click on the region opens: the first that has a card, else the first. */
export function preferredItemId(region, items) {
  const ids = asArray(region?.itemIds);
  return ids.find((id) => asArray(items?.get(id)?.cards).length) ?? ids[0] ?? null;
}

function stripPrefix(name, prefix) {
  return prefix && name.startsWith(prefix) ? name.slice(prefix.length) : name;
}

/** `[first, last]` of an end's `location.lines`, which may name one line; else null. */
function lineRange(lines) {
  const list = asArray(lines);
  return (list.length === 1 || list.length === 2) && list.every(positiveInteger) ? [list[0], list.at(-1)] : null;
}

/** "Line 7", "Lines 7–9", or "". */
export function linesText(range) {
  const [first, last] = asArray(range);
  return !first ? "" : first === last ? `Line ${first}` : `Lines ${first}–${last}`;
}

/**
 * One card: an object end of a record. The overlay's own ends say in their
 * extension whether there is a rendered page (`cardHref`); an end from
 * elsewhere does not, and its `uri` is taken as the page. A card is named by
 * its declaration, else the end's `title` (an end from elsewhere), else the
 * extension's declaration id.
 */
function card(record, end, context) {
  const ext = extensionOf(end);
  const recordExt = extensionOf(record);
  const location = isObject(end.location) ? end.location : {};
  const declaration = isObject(ext.declaration) ? ext.declaration : {};
  const destination = isObject(ext.destination) ? ext.destination : null;
  const fullName = text(location.declaration) || text(end.title) || text(declaration.id) || text(end.uri);
  const ours = isObject(end.extensions) && isObject(end.extensions[NAMESPACE]);
  const href = ours ? cardHref(end, context.siteUrl, context.pageOrigin)
    : targetHref(end.uri, context.baseUrl, context.pageOrigin);
  const badges = [];
  if (declaration.private) {
    badges.push("private");
  }
  if (declaration.anonymous) {
    badges.push("anonymous");
  }
  if (text(declaration.sorry) && declaration.sorry !== "none") {
    badges.push(`sorry: ${declaration.sorry}`);
  }
  const snippet = isObject(ext.snippet) && text(ext.snippet.text)
    ? { line: Number.isInteger(ext.snippet.line) ? ext.snippet.line : null, text: ext.snippet.text,
        ...(ext.snippet.truncated === true ? { truncated: true } : {}) }
    : null;
  const verdict = isObject(ext.declaration_verdict) && text(ext.declaration_verdict.verdict)
    ? {
      note: text(ext.declaration_verdict.note),
      status: text(ext.declaration_verdict.status),
      verdict: text(ext.declaration_verdict.verdict),
    }
    : null;
  const declarationId = text(declaration.id) || fullName;
  return {
    badges,
    declarationId,
    fallbackLabel: destination?.resolution === "fallback" ? text(destination.fallback_label) : "",
    fullName,
    href,
    id: [text(record.id), text(end.role), declarationId].join("/"),
    kind: text(declaration.kind),
    lines: lineRange(location.lines),
    module: text(location.module),
    name: stripPrefix(fullName, context.stripPrefix) || fullName,
    provenanceMethod: isObject(recordExt.provenance) ? text(recordExt.provenance.method) : "",
    reviewed: isObject(recordExt.provenance) && typeof recordExt.provenance.confirmed_by_human === "boolean"
      ? recordExt.provenance.confirmed_by_human : null,
    resolution: text(destination?.resolution),
    role: text(end.role),
    proofContext: proofContext(ext.proof_context, href, end.title),
    snippet,
    target: ours ? cardTarget(end) : null,
    verdict,
  };
}

/** Optional, authored explanations. Source links stay on the card's already validated URL. */
function proofContext(value, href, title) {
  if (!isObject(value) || !text(value.declaration) || !text(value.conclusion)) return null;
  const lines = lineRange(value.lines);
  let enclosingHref = null;
  if (href && lines) {
    const url = new URL(href);
    url.hash = `L${lines[0]}-L${lines[1]}`;
    enclosingHref = url.href;
  }
  return {
    declaration: text(value.declaration), lines, href: enclosingHref,
    label: text(title) || text(value.conclusion),
    conclusion: text(value.conclusion), explanation: text(value.explanation),
    bookkeeping: text(value.bookkeeping),
    notation: asArray(value.notation).filter(isObject)
      .map(row => ({ math: text(row.math), lean: text(row.lean) })).filter(row => row.math && row.lean),
  };
}

function proofGuide(value) {
  if (!isObject(value) || !["direct", "helper", "chain"].includes(value.kind)) return null;
  return { kind: value.kind, summary: text(value.summary) };
}

/**
 * What a checked absence names: the Lean its record looked in, by declaration,
 * else by module, with its lines when it gives them. It carries no link of its
 * own: `linkSteps` borrows one from a card of the same proof.
 */
function checkedEnd(end, context) {
  const location = isObject(end.location) ? end.location : {};
  const fullName = text(location.declaration) || text(end.title);
  return {
    fullName,
    href: null,
    lines: lineRange(location.lines),
    module: text(location.module),
    name: stripPrefix(fullName, context.stripPrefix) || text(location.module),
  };
}

/** By role, then by card id, so the order does not depend on the order the files came in. */
function orderCards(cards) {
  return cards
    .map((entry, index) => ({ entry, index }))
    .sort((left, right) => {
      const a = LINK_ROLES.indexOf(left.entry.role);
      const b = LINK_ROLES.indexOf(right.entry.role);
      const rank = (a < 0 ? LINK_ROLES.length : a) - (b < 0 ? LINK_ROLES.length : b);
      const byId = left.entry.id < right.entry.id ? -1 : left.entry.id > right.entry.id ? 1 : 0;
      return rank || byId || left.index - right.index;
    })
    .map(({ entry }) => entry);
}

function ends(record, side) {
  return isObject(record) ? asArray(record[side]).filter(isObject) : [];
}

/** A record's subject ends that are regions of the PDF whose overlay extension carries `sourceId`. */
function sourceEnds(record, sourceId) {
  return ends(record, "subject").filter((end) => end.type === "pdf" && extensionOf(end).source_id === sourceId);
}

/**
 * The PDF the config names: a subject end of the records whose overlay
 * extension carries its `source_id`. Every such end must cite the same bytes;
 * two different sha256 under one id is a refusal.
 */
export function pdfSource(files, sourceId) {
  let found = null;
  for (const file of asArray(files)) {
    for (const record of asArray(file.records)) {
      for (const end of sourceEnds(record, sourceId)) {
        const sha256 = text(end.sha256).toLowerCase();
        if (!found) {
          found = { sha256, source: end };
        } else if (found.sha256 !== sha256) {
          fail(`The records of ${JSON.stringify(sourceId)} cite two different PDFs.`);
        }
      }
    }
  }
  if (!found) {
    fail(`No annotation is a record of the PDF ${JSON.stringify(sourceId)}, which overlay.config.json `
         + "names.");
  }
  if (!/^[a-f\d]{64}$/.test(found.sha256)) {
    fail(`The PDF ${JSON.stringify(sourceId)} states no sha256, so it cannot be verified.`);
  }
  return found.source;
}

/** The site root a file's URL is under: its URL less its path (`<directory>/...`). */
function siteRootOf(file) {
  const url = text(file?.url);
  const path = text(file?.path);
  return path && url.endsWith(path) ? url.slice(0, url.length - path.length) : url;
}

export function isStep(item) {
  return item?.kind === STEP_KIND;
}

const NATURAL = new Intl.Collator("en", { numeric: true });

/**
 * Tie each step to its item. The overlay's layout gives an item with several
 * records a folder of its own, so a step's item is the one other item with
 * records in the step's folder; steps in a folder with none, or with several,
 * stay items of their own, never guessed onto one. A proof's steps are in the
 * natural order of their file names. Then a checked absence's declaration links
 * where a card of the same proof that names it links.
 */
function linkSteps(items, folders) {
  for (const { owners, steps } of folders.values()) {
    if (owners.size === 1) {
      const [owner] = owners;
      const ordered = [...steps].filter(([id]) => id !== owner)
        .sort((left, right) => NATURAL.compare(left[1], right[1]));
      for (const [id] of ordered) {
        items.get(id).stepOf = owner;
        items.get(owner).steps.push(id);
      }
    }
  }
  for (const item of items.values()) {
    const owner = items.get(item.stepOf);
    const proof = owner ? [owner, ...owner.steps.map((id) => items.get(id))] : [item];
    for (const end of item.checked) {
      end.href = (end.fullName && proof.flatMap((member) => member.cards)
        .find((entry) => entry.fullName === end.fullName)?.href) || null;
    }
  }
}

/**
 * The page → region → item → card index the viewer renders, built purely from
 * the annotation files (`[{url, path, records}]`, each url the file's own) and
 * the configuration. `siteUrl` is the site root; without it, each file's URL
 * less its path.
 *
 * A record is about one item, its `id`: each of its subject ends that is a
 * region of the PDF adds the item to that region (the extension's `region`
 * id), and each object end adds one card to the item (once per item, however
 * many records repeat it), unless the record is a checked absence
 * (`noMatch`). `warnings` holds one line per region this build could not
 * place; such a region is dropped, never guessed at, and a record with no
 * region left adds nothing.
 *
 * An item of kind `STEP_KIND` is a step: its relation is kept, its cards stay
 * in the record's order, and the ends of its checked absence are `checked`,
 * never cards. A step belongs to the one other item with records in its item
 * folder (`linkSteps`).
 */
export function buildIndex(files, config, { pageOrigin = "", siteUrl = null } = {}) {
  const source = pdfSource(files, config.sourceId);
  const sourceExt = extensionOf(source);
  const pageCount = Number.isInteger(sourceExt.physical_page_count) ? sourceExt.physical_page_count : null;
  const items = new Map();
  const regions = new Map();
  const seen = new Map();
  const folders = new Map();
  const warnings = [];
  for (const file of asArray(files)) {
    const context = {
      baseUrl: file.url, pageOrigin, siteUrl: siteUrl ?? siteRootOf(file), stripPrefix: config.stripPrefix ?? "",
    };
    for (const record of asArray(file.records)) {
      const placed = [];
      for (const end of sourceEnds(record, config.sourceId)) {
        let place;
        try {
          place = endRegion(end);
        } catch (error) {
          warnings.push(`${text(record.id) || file.url}: ${error instanceof Error ? error.message : error}`);
          continue;
        }
        if (pageCount !== null && place.page > pageCount) {
          warnings.push(`${text(record.id)}: page ${place.page} is past the source's ${pageCount} pages.`);
          continue;
        }
        placed.push({ end, place });
      }
      if (!placed.length) {
        continue;
      }
      const ext = extensionOf(record);
      const title = placed.map(({ end }) => text(end.title)).find(Boolean) ?? "";
      const itemId = text(record.id) || title || `p${placed[0].place.page}`;
      if (!items.has(itemId)) {
        items.set(itemId, {
          cards: [],
          checked: [],
          clusterIds: asArray(ext.clusters).map(text).filter(Boolean),
          coverage: text(ext.coverage) || text(record.status),
          coverageNote: text(record.note),
          id: itemId,
          kind: text(record.kind),
          label: title || itemId,
          locator: locatorText(ext.locator),
          place: null,
          proofGuide: proofGuide(ext.proof),
          relations: [],
          stepOf: null,
          steps: [],
          verdicts: asArray(ext.verdicts).filter(isObject),
        });
        seen.set(itemId, new Set());
      }
      const item = items.get(itemId);
      const relation = text(record.relation);
      if (relation && !item.relations.includes(relation)) {
        item.relations.push(relation);
      }
      if (file.folder) {
        if (!folders.has(file.folder)) {
          folders.set(file.folder, { owners: new Set(), steps: new Map() });
        }
        const folder = folders.get(file.folder);
        if (record.kind !== STEP_KIND) {
          folder.owners.add(itemId);
        } else if (!folder.steps.has(itemId)) {
          folder.steps.set(itemId, text(file.path));
        }
      }
      const absent = record.relation === "noMatch";
      if (!absent || isStep(item)) {
        for (const end of ends(record, "object")) {
          const key = [text(end.role), text(end.uri), JSON.stringify(end.location ?? null)].join("\n");
          if (!seen.get(itemId).has(key)) {
            seen.get(itemId).add(key);
            if (absent) {
              item.checked.push(checkedEnd(end, context));
            } else {
              item.cards.push(card(record, end, context));
            }
          }
        }
      }
      for (const { end, place } of placed) {
        const endExt = extensionOf(end);
        const regionId = text(endExt.region) || `p${place.page}:${end.location.rect.join(",")}`;
        if (!regions.has(regionId)) {
          regions.set(regionId, { id: regionId, itemIds: [], order: [], page: place.page, quads: place.quads });
        }
        const region = regions.get(regionId);
        if (!region.itemIds.includes(itemId)) {
          region.itemIds.push(itemId);
        }
        if (!region.order.length) {
          region.order = asArray(endExt.region_items).map(text).filter(Boolean);
        }
      }
    }
  }
  for (const item of items.values()) {
    if (!isStep(item)) {
      item.cards = orderCards(item.cards);
    }
  }
  linkSteps(items, folders);
  const pages = new Map();
  for (const region of regions.values()) {
    // The curation's order (the item the region's own label names first), not file order.
    const rank = (id) => (region.order.includes(id) ? region.order.indexOf(id) : region.order.length);
    region.itemIds = region.itemIds
      .map((id, index) => ({ id, index }))
      .sort((left, right) => rank(left.id) - rank(right.id) || left.index - right.index)
      .map(({ id }) => id);
    delete region.order;
    region.label = region.itemIds.map((id) => items.get(id).label).join(" + ");
    if (!pages.has(region.page)) {
      pages.set(region.page, []);
    }
    pages.get(region.page).push(region);
  }
  // Reading order on each page, top to bottom and then left to right, whatever order the files came in.
  const corner = (region) => region.quads.flat().reduce(([y, x], point) => [Math.min(y, point[1]), Math.min(x, point[0])],
    [Infinity, Infinity]);
  for (const regionsOnPage of pages.values()) {
    regionsOnPage.sort((left, right) => {
      const [ly, lx] = corner(left);
      const [ry, rx] = corner(right);
      return ly - ry || lx - rx || (left.id < right.id ? -1 : left.id > right.id ? 1 : 0);
    });
  }
  // Where a row of another page opens an item: its first region in reading order.
  for (const page of [...pages.keys()].sort((a, b) => a - b)) {
    for (const region of pages.get(page)) {
      for (const id of region.itemIds) {
        items.get(id).place ??= { page, regionId: region.id };
      }
    }
  }
  return {
    bytes: Number.isInteger(sourceExt.bytes) ? sourceExt.bytes : null,
    items,
    pageCount,
    pages,
    regions: [...regions.values()],
    sha256: text(source.sha256).toLowerCase(),
    source,
    sourceTitle: text(isObject(source.citation) ? source.citation.title : ""),
    warnings,
  };
}

// --- the card list ----------------------------------------------------------

export function cardsMatching(cards, filters = {}) {
  const query = text(filters.query).toLocaleLowerCase();
  return asArray(cards).filter((entry) => {
    if (!query) {
      return true;
    }
    return [entry.fullName, entry.name, entry.module, entry.kind, entry.declarationId]
      .join("\n")
      .toLocaleLowerCase()
      .includes(query);
  });
}

export function paginateCards(cards, requestedPage, pageSize = CARD_PAGE_SIZE) {
  if (!Number.isInteger(pageSize) || pageSize <= 0) {
    throw new Error("The card page size must be a positive integer.");
  }
  const pageCount = Math.max(1, Math.ceil(cards.length / pageSize));
  const page = Math.min(Math.max(Number(requestedPage) || 1, 1), pageCount);
  return {
    items: cards.slice((page - 1) * pageSize, page * pageSize),
    page,
    pageCount,
    total: cards.length,
  };
}

// --- the bridge view --------------------------------------------------------

/** One word per coverage status, for the pill; its title is the status's summary. */
export const COVERAGE_WORDS = Object.freeze({
  example_only: "Examples only",
  formalized: "Formalized",
  not_formalized: "Not formalized",
  out_of_scope: "Out of scope",
  partial: "Partial",
  private_only: "Private only",
  support_only: "Support only",
});

/** The coverage pill's word, or "" for an unknown status. */
export function coverageWord(coverage) {
  return COVERAGE_WORDS[coverage] ?? "";
}

/** The statuses whose region is drawn dashed: something, but not a key statement of the item. */
const PARTIAL_COVERAGE = Object.freeze(["partial", "support_only", "example_only", "private_only"]);

/** A parenthetical that numbers rather than names: "(A.1)", "(3.2b)", "(ii)", "(c)". */
const NUMBERING = /^(?:\p{L}?[\d.]*\d\p{L}?|[ivxlc]+|\p{L})$/iu;

const SUBSCRIPTS = "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓₔₕₖₗₘₙₚₛₜᵢᵣᵤᵥᵦᵧᵨᵩᵪⱼ";
const SUPERSCRIPTS = "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁱⁿʰʲʳʷʸˡˢˣᴬᴮᴰᴱᴳᴴᴵᴶᴷᴸᴹᴺᴼᴾᴿᵀᵁⱽᵂᵃᵇᶜᵈᵉᶠᵍᵏᵐᵒᵖᵗᵘᵛᶻᵝᵞᵟᵠᵡ";
const SCRIPT_RUN = new RegExp(`([_^])\\{|((?:[${SUBSCRIPTS}]\\p{M}*)+)|((?:[${SUPERSCRIPTS}]\\p{M}*)+)`, "gu");

/**
 * A title cut into text/sub/sup runs. Unicode script characters are normalized
 * to plain letters; braced scripts retain characters Unicode cannot express,
 * such as a capital S or [r,s]. Braces may nest, but no TeX commands or HTML
 * are interpreted. The renderer handles each script's contents the same way.
 */
export function scriptRuns(value) {
  const whole = text(value);
  const runs = [];
  let at = 0;
  SCRIPT_RUN.lastIndex = 0;
  for (let match; (match = SCRIPT_RUN.exec(whole));) {
    let end = SCRIPT_RUN.lastIndex;
    let kind, content;
    if (match[1]) {
      let depth = 1;
      while (end < whole.length && depth) {
        if (whole[end] === "{") depth += 1;
        if (whole[end] === "}") depth -= 1;
        end += 1;
      }
      // A malformed or empty script remains literal text.
      if (depth || end === SCRIPT_RUN.lastIndex + 1) continue;
      kind = match[1] === "_" ? "sub" : "sup";
      content = whole.slice(SCRIPT_RUN.lastIndex, end - 1);
      SCRIPT_RUN.lastIndex = end;
    } else {
      kind = match[2] ? "sub" : "sup";
      content = match[0].normalize("NFKC");
    }
    if (match.index > at) runs.push(["text", whole.slice(at, match.index)]);
    runs.push([kind, content]);
    at = end;
  }
  if (at < whole.length) runs.push(["text", whole.slice(at)]);
  return runs;
}

/**
 * A label's title and its trailing parenthetical, when that names something:
 * "Theorem 2 (Main estimate)" is "Theorem 2" over "Main estimate", while
 * "Equation (1.1)", "Equation (A.1)" and "Exercise 4 (c)" stay whole.
 */
export function labelParts(label) {
  const whole = text(label);
  const match = /^(.*\S)\s+\(([^()]*\p{L}[^()]*)\)$/u.exec(whole);
  return match && !NUMBERING.test(match[2].trim())
    ? { subtitle: match[2].trim(), title: match[1] } : { subtitle: "", title: whole };
}

/**
 * The locator without the label it repeats, and only its first locator: the
 * comma-separated segments, less one equal to the label or to its title, cut
 * at the first ";" or " / " that starts another. A locator that never repeats
 * the label is kept whole up to that cut, and one that is only the label is "".
 */
export function locatorTail(label, locator) {
  const full = text(locator);
  if (!full) {
    return "";
  }
  const head = full.split(/\s*;\s+|\s+\/\s+/)[0].trim() || full;
  const drop = new Set([text(label), labelParts(label).title].filter(Boolean));
  const parts = head.split(", ");
  const kept = parts.filter((part) => !drop.has(part));
  return kept.length === parts.length ? head : kept.join(", ");
}

/** `[namespace with its trailing ".", leaf]` of a declaration name; a name with no "." is all leaf. */
export function splitName(name) {
  const value = typeof name === "string" ? name : "";
  const cut = value.lastIndexOf(".", value.length - 2);
  return cut > 0 ? [value.slice(0, cut + 1), value.slice(cut + 1)] : ["", value];
}

/** An item's first key card, in card order, or null: the name the page index and the peek show. */
export function firstKeyCard(item) {
  return asArray(item?.cards).find((entry) => entry.role === "key") ?? null;
}

/**
 * A step's cards, one per declaration in the record's order, each with its
 * `ranges`: `{lines, snippet}` per end that names lines or a snippet.
 */
export function stepCards(item) {
  const groups = new Map();
  for (const entry of asArray(item?.cards)) {
    const key = [entry.fullName, entry.module, entry.href ?? ""].join("\n");
    if (!groups.has(key)) {
      groups.set(key, { ...entry, ranges: [] });
    }
    if (entry.lines || entry.snippet) {
      groups.get(key).ranges.push({ lines: entry.lines, snippet: entry.snippet, target: entry.target });
    }
  }
  return [...groups.values()];
}

/**
 * How a step reads: `title` "Step 3" and its `number` among the `of` steps of
 * its proof, and `text`, its label less the id it repeats. A step tied to no
 * item is titled by its whole label.
 */
export function stepParts(item, items) {
  const label = text(item?.label);
  const id = text(item?.id);
  const steps = asArray(items?.get(item?.stepOf)?.steps);
  const at = steps.indexOf(item?.id);
  if (at < 0) {
    return { number: null, of: null, text: "", title: label };
  }
  return {
    number: at + 1,
    of: steps.length,
    text: label === id ? "" : label.startsWith(`${id}: `) ? label.slice(id.length + 2).trim() : label,
    title: `Step ${at + 1}`,
  };
}

/**
 * A step's relation as printed: its `word` and `title`. A minimal overlay
 * prints only what qualifies the link, as it does with the coverage word: an
 * exact match goes unmarked, so a step with no word there is an exact one.
 */
export function stepRelation(item, { minimal = false } = {}) {
  const shown = asArray(item?.relations).filter((relation) => !minimal || relation !== "exactMatch");
  // The guide describes the correspondence, not a stronger claim of formal equivalence.
  // Absence and broader/narrower claims must never be hidden by an authored guide.
  const guide = isStep(item) && item.proofGuide;
  if (guide && asArray(item.relations).every(r => ["exactMatch", "closeMatch"].includes(r))) {
    const count = stepCards(item).length;
    return {
      word: guide.kind === "chain" ? `${count} linked lemmas`
        : guide.kind === "helper" ? "Helper lemma" : "Proof excerpt",
      title: [guide.summary, ...shown.map(r => RELATION_SUMMARIES[r] ?? "")].filter(Boolean).join(" "),
    };
  }
  return {
    title: shown.map((relation) => RELATION_SUMMARIES[relation] ?? "").filter(Boolean).join(" "),
    word: shown.map((relation) => RELATION_WORDS[relation] ?? relation).join(", "),
  };
}

/**
 * The page index's rows: `{item, region, steps: [{item, region}]}`, in the
 * order the page's regions show them, each step under its item. A step whose
 * item has no region on this page brings the item with it, with `region` null.
 */
export function pageRows(regions, items) {
  const rows = new Map();
  const seen = new Set();
  const row = (item) => {
    if (!rows.has(item.id)) {
      rows.set(item.id, { item, region: null, steps: [] });
    }
    return rows.get(item.id);
  };
  for (const region of asArray(regions)) {
    for (const id of region.itemIds) {
      const item = items.get(id);
      if (!seen.has(id)) {
        seen.add(id);
        if (item.stepOf) {
          row(items.get(item.stepOf)).steps.push({ item, region });
        } else {
          row(item).region = region;
        }
      }
    }
  }
  for (const { item, steps } of rows.values()) {
    steps.sort((left, right) => item.steps.indexOf(left.item.id) - item.steps.indexOf(right.item.id));
  }
  return [...rows.values()];
}

/**
 * An item's cards as the item view lists them: `keys`, each shown with its
 * statement, and `related`, every other role, one line each.
 */
export function splitCards(item) {
  const cards = asArray(item?.cards);
  return {
    keys: cards.filter((entry) => entry.role === "key"),
    related: cards.filter((entry) => entry.role !== "key"),
  };
}

/** The role more than half of `cards` share, said once in the Related heading; "" when none does. */
export function sharedRole(cards) {
  const list = asArray(cards);
  const counts = new Map();
  for (const entry of list) {
    counts.set(entry.role, (counts.get(entry.role) ?? 0) + 1);
  }
  const [role, most] = [...counts].sort((a, b) => b[1] - a[1])[0] ?? ["", 0];
  return role && most * 2 > list.length ? role : "";
}

/** How a region draws its item: "formalized" (solid), "partial" (dashed) or "quiet" (dotted). */
export function regionCoverage(item) {
  const coverage = text(item?.coverage);
  if (coverage === "formalized") {
    return "formalized";
  }
  if (PARTIAL_COVERAGE.includes(coverage)) {
    return "partial";
  }
  if (coverage === "not_formalized" || coverage === "out_of_scope") {
    return "quiet";
  }
  return firstKeyCard(item) ? "formalized" : "quiet";
}

/** Verdict kinds, most serious first: the order a region's stroke picks from. */
export const VERDICT_SEVERITY = Object.freeze(["deviation", "attribution_gap", "proxy", "convention", "faithful"]);

/**
 * The verdict kind a region's stroke shows: the most serious open item
 * verdict of its items (an item with none falls back on its cards'
 * declaration verdicts), else "fixed" when every verdict is a fixed one, else
 * null.
 */
export function regionVerdict(items) {
  const open = new Set();
  let fixed = false;
  for (const item of asArray(items)) {
    const own = asArray(item?.verdicts).filter(isObject);
    const verdicts = own.length ? own : asArray(item?.cards).map((entry) => entry.verdict).filter(isObject);
    for (const verdict of verdicts) {
      if (verdict.status === "fixed" && verdict.verdict !== "faithful") {
        fixed = true;
      } else if (VERDICT_SEVERITY.includes(verdict.verdict)) {
        open.add(verdict.verdict);
      }
    }
  }
  return VERDICT_SEVERITY.find((kind) => open.has(kind)) ?? (fixed ? "fixed" : null);
}

/** How many passages a page has, and how many of them a key declaration states. */
export function pageSummary(items) {
  const list = asArray(items);
  return { passages: list.length, withKey: list.filter((item) => firstKeyCard(item)).length };
}

/** The count line under the page index's heading; `steps` is how many proof steps the page holds. */
export function pageSummaryText(items, steps = 0) {
  const { passages, withKey } = pageSummary(items);
  const parts = [passages ? `${plural(passages, "passage")} · ${withKey} with a key declaration` : "",
                 steps ? plural(steps, "proof step") : ""].filter(Boolean);
  return parts.length ? parts.join(" · ") : "No highlighted passages on this page.";
}

/** Whether a verdict was reviewed: true, false, or null when it does not say. */
function verdictReviewed(verdict) {
  if (verdict?.status === "unconfirmed") {
    return false;
  }
  if (verdict?.status === "human_confirmed") {
    return true;
  }
  const provenance = isObject(verdict?.provenance) ? verdict.provenance : {};
  return typeof provenance.confirmed_by_human === "boolean" ? provenance.confirmed_by_human : null;
}

/** ", unreviewed." / ", reviewed." / ", partly reviewed." from review flags, or "." when none says. */
function reviewSuffix(flags) {
  const known = flags.filter((flag) => typeof flag === "boolean");
  if (!known.length) {
    return ".";
  }
  if (known.every((flag) => !flag)) {
    return ", unreviewed.";
  }
  return known.every(Boolean) ? ", reviewed." : ", partly reviewed.";
}

/** The page index's footer when verdicts are shown: "Machine verdicts, unreviewed.", or "". */
export function verdictFooter(items) {
  const verdicts = asArray(items).flatMap((item) => [
    ...asArray(item?.verdicts).filter(isObject),
    ...asArray(item?.cards).map((entry) => entry.verdict).filter(isObject),
  ]);
  return verdicts.length ? `Machine verdicts${reviewSuffix(verdicts.map(verdictReviewed))}` : "";
}

/**
 * The item view's one footer line: how the links were made (each distinct
 * provenance method), whether verdicts are shown, and whether any of it was
 * reviewed by a person. "" for an item with no card and no verdict shown.
 */
export function reviewFooter(item, { faithfulness = false } = {}) {
  const cards = asArray(item?.cards);
  const verdicts = faithfulness
    ? [...asArray(item?.verdicts).filter(isObject), ...cards.map((entry) => entry.verdict).filter(isObject)]
    : [];
  if (!cards.length && !verdicts.length) {
    return "";
  }
  const methods = [...new Set(cards.map((entry) => text(entry.provenanceMethod)).filter(Boolean))];
  const flags = [...cards.map((entry) => (typeof entry.reviewed === "boolean" ? entry.reviewed : null)),
                 ...verdicts.map(verdictReviewed)];
  const suffix = reviewSuffix(flags);
  if (!methods.length && !verdicts.length && suffix === ".") {
    // "Links." says nothing the page does not.
    return "";
  }
  const links = cards.length ? (methods.length ? `${methods.join(" and ")} links` : "links") : "";
  const subject = [links, verdicts.length ? "machine verdicts" : ""].filter(Boolean).join(" and ");
  return `${subject.charAt(0).toUpperCase()}${subject.slice(1)}${suffix}`;
}

const LEAN_KEYWORDS = new Set([
  "theorem", "lemma", "def", "abbrev", "structure", "class", "instance", "inductive", "example", "axiom",
  "opaque", "noncomputable", "private", "protected", "partial", "unsafe", "deriving", "where", "fun", "let",
  "have", "show", "by", "from", "with", "match", "if", "then", "else", "Prop", "Type", "Sort",
]);
/** What may not stand next to a keyword: an identifier character, ASCII or not. */
const WORD = /[A-Za-z0-9_'.!?\p{L}\p{N}]/u;

/**
 * A Lean snippet as `[kind, text]` runs for a light tint: "kw" for a keyword
 * standing alone, "cm" for a comment (`--` to the end of the line, or `/-`
 * through `-/`), "el" for the ellipsis a snippet is cut with, "tx" for the
 * rest. The texts joined are the input, exactly.
 */
export function leanTokens(source) {
  const input = typeof source === "string" ? source : "";
  const runs = [];
  const push = (kind, value) => {
    if (!value) {
      return;
    }
    const last = runs[runs.length - 1];
    if (last && last[0] === kind && kind === "tx") {
      last[1] += value;
    } else {
      runs.push([kind, value]);
    }
  };
  let at = 0;
  while (at < input.length) {
    if (input.startsWith("--", at)) {
      const end = input.indexOf("\n", at);
      const stop = end < 0 ? input.length : end;
      push("cm", input.slice(at, stop));
      at = stop;
    } else if (input.startsWith("/-", at)) {
      const end = input.indexOf("-/", at + 2);
      const stop = end < 0 ? input.length : end + 2;
      push("cm", input.slice(at, stop));
      at = stop;
    } else if (input[at] === "…") {
      push("el", "…");
      at += 1;
    } else if (/[A-Za-z]/.test(input[at]) && (at === 0 || !WORD.test(input[at - 1]))) {
      let end = at;
      while (end < input.length && /[A-Za-z]/.test(input[end])) {
        end += 1;
      }
      const word = input.slice(at, end);
      const after = input[end];
      const standalone = after === undefined || !WORD.test(after);
      if (standalone && LEAN_KEYWORDS.has(word)) {
        push("kw", word);
        at = end;
      } else {
        // The rest of this identifier, so a keyword never matches inside it.
        while (end < input.length && WORD.test(input[end]) && !input.startsWith("--", end)
               && !input.startsWith("/-", end) && input[end] !== "…") {
          end += 1;
        }
        push("tx", input.slice(at, end));
        at = end;
      }
    } else {
      push("tx", input[at]);
      at += 1;
    }
  }
  return runs;
}

// --- Verso statements and hovers --------------------------------------------

/** `build_site.py` writes it beside the bundle, from the built site (docs/overlay.md section 5). */
export const VERSO_URL = "../verso.json";
export const VERSO_SCHEMA = "formal-companion/overlay-verso";
/** The run kinds `tools/verso_extract.py` writes; any other is plain text. */
export const RUN_KINDS = Object.freeze(["tx", "kw", "cm", "el", "const", "var", "sort", "lit"]);

function positiveInteger(value) {
  return Number.isInteger(value) && value > 0 ? value : null;
}

/** One run `[kind, text, hover, binding, href]`, every member checked; null when it is not one. */
function readRun(run, hovers) {
  if (!Array.isArray(run) || typeof run[0] !== "string" || typeof run[1] !== "string" || !run[1]) {
    return null;
  }
  const hover = Number.isInteger(run[2]) && run[2] >= 0 ? run[2] : null;
  return [RUN_KINDS.includes(run[0]) ? run[0] : "tx", run[1],
          hover !== null && hovers.has(String(hover)) ? String(hover) : null,
          positiveInteger(run[3]), typeof run[4] === "string" && run[4] ? run[4] : null];
}

/**
 * `verso.json`: each card target's statements as runs, and the hover entries
 * they use, `{signature, doc}` as plain text. A statement with a run that does
 * not read is dropped, never repaired; a file that is not one is null, and the
 * cards keep their plain statements.
 */
export function readVerso(raw) {
  if (!isObject(raw) || raw.schema !== VERSO_SCHEMA || raw.schema_version !== 1
      || !isObject(raw.statements) || !isObject(raw.hovers)) {
    return null;
  }
  const hovers = new Map();
  for (const [id, entry] of Object.entries(raw.hovers)) {
    const signature = isObject(entry) && typeof entry.signature === "string" ? entry.signature : "";
    const doc = isObject(entry) && typeof entry.doc === "string" ? entry.doc : "";
    if (/^\d+$/.test(id) && (signature || doc)) {
      hovers.set(id, { doc, signature });
    }
  }
  const statements = new Map();
  for (const [target, alternatives] of Object.entries(raw.statements)) {
    const read = asArray(alternatives).map((runs) => {
      const list = Array.isArray(runs) ? runs.map((run) => readRun(run, hovers)) : [null];
      return list.length && list.every(Boolean) ? list : null;
    }).filter(Boolean);
    if (read.length) {
      statements.set(target, read);
    }
  }
  return { hovers, statements };
}

/**
 * A card's statement as Verso runs, or null. Only a statement whose texts
 * joined are the card's snippet, exactly, is used, so a stale `verso.json`
 * never changes what a card says.
 */
export function versoRuns(verso, card) {
  const source = card?.snippet?.text;
  const alternatives = card?.target && typeof source === "string" ? verso?.statements?.get(card.target) : null;
  return asArray(alternatives).find((runs) => runs.map((run) => run[1]).join("") === source) ?? null;
}

/** A run's hover: its entry's signature, a variable's as `name : type`, and its docstring; or null. */
export function hoverText(verso, run) {
  const entry = Array.isArray(run) && run[2] ? verso?.hovers?.get(run[2]) : null;
  if (!entry) {
    return null;
  }
  const signature = entry.signature && run[0] === "var" ? `${run[1].trim()} : ${entry.signature}` : entry.signature;
  return { doc: entry.doc, signature };
}

const LIST_ITEM = /^\s*(?:[-*+]|\d+[.)])\s/;

/** Inline code in a docstring's paragraph: `[["tx" | "code", text], ...]`. */
function inlineCode(paragraph) {
  const parts = [];
  let rest = paragraph;
  for (let match = /(`+)([\s\S]*?[^`])\1(?!`)/.exec(rest); match; match = /(`+)([\s\S]*?[^`])\1(?!`)/.exec(rest)) {
    if (match.index) {
      parts.push(["tx", rest.slice(0, match.index)]);
    }
    parts.push(["code", match[2].trim() || match[2]]);
    rest = rest.slice(match.index + match[0].length);
  }
  if (rest) {
    parts.push(["tx", rest]);
  }
  return parts;
}

/**
 * A docstring's Markdown as plain blocks: `{code: true, parts}` for a fenced
 * block, else a paragraph of text and inline code. A paragraph's lines are
 * joined with spaces, except that a list item starts a line of its own.
 * Nothing else is interpreted: what is not code is shown as written.
 */
export function docBlocks(markdown) {
  const blocks = [];
  let paragraph = [];
  const flush = () => {
    if (paragraph.length) {
      const joined = paragraph.map((line, index) => (index && LIST_ITEM.test(line) ? `\n${line.trim()}`
        : `${index ? " " : ""}${line.trim()}`)).join("");
      blocks.push({ code: false, parts: inlineCode(joined) });
    }
    paragraph = [];
  };
  const lines = (typeof markdown === "string" ? markdown : "").split("\n");
  for (let at = 0; at < lines.length; at += 1) {
    const fence = /^\s*(```+|~~~+)/.exec(lines[at]);
    if (fence) {
      flush();
      const body = [];
      for (at += 1; at < lines.length && !lines[at].trim().startsWith(fence[1]); at += 1) {
        body.push(lines[at]);
      }
      blocks.push({ code: true, parts: [["code", body.join("\n")]] });
    } else if (lines[at].trim()) {
      paragraph.push(lines[at]);
    } else {
      flush();
    }
  }
  flush();
  return blocks;
}
