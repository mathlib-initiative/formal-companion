/**
 * The viewer's text layer, driven by the vendored PDF.js build itself.
 *
 * `viewer-controller.test.mjs` drives the controller with a stand-in for
 * `TextLayer`; this suite runs the real one over a synthetic one-line PDF, in
 * a DOM shim with just what it touches (an element with a style, a body to
 * append a measuring canvas to, and a 2d context that can measure text). It
 * then pins `viewer.css` against what that run wrote: every custom property
 * and every `data-main-rotation` the build sets is read by a rule, since only
 * the build files are vendored (`vendor/pdfjs/PINNED.md`) and the rules they
 * are written for are the viewer's own. The one exception is
 * `--min-font-size`, which those rules leave alone on purpose and which this
 * suite pins as unread.
 *
 * viewer.js is imported before the shim is installed, deliberately: it
 * bootstraps itself when a `document` is already there.
 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import { startTextLayer } from "../runtime/viewer/viewer.js";
import { VIEWER_DIR, fakeStyle } from "./fixtures.mjs";

const VENDOR = new URL("../runtime/vendor/pdfjs/", import.meta.url);

/** One page, 200 x 200 pt, with one word at (20, 100); no xref table, as PDF.js scans for the objects. */
const PDF = [
  "%PDF-1.4",
  "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
  "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
  "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R",
  "/Resources << /Font << /F1 5 0 R >> >> >> endobj",
  "4 0 obj << /Length 39 >> stream",
  "BT /F1 12 Tf 20 100 Td (Passage) Tj ET",
  "endstream endobj",
  "5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj",
  "trailer << /Root 1 0 R >>",
].join("\n");

/**
 * What this run touches, which is not what the controller's `fakeElement`
 * touches: a tree with parents, a measuring 2d context and a bounding box.
 */
function shimElement(tag) {
  const element = {
    attributes: {},
    children: [],
    parent: null,
    style: fakeStyle(),
    tagName: tag.toUpperCase(),
    textContent: "",
    append(...nodes) {
      for (const node of nodes) {
        node.parent = element;
        element.children.push(node);
      }
    },
    getBoundingClientRect: () => ({ bottom: 1, height: 1, left: 0, right: 1, top: 0, width: 1 }),
    getContext() {
      element.context ??= {
        canvas: element,
        font: "10px sans-serif",
        measureText(text) {
          const size = Number.parseFloat(this.font) || 10;
          return { width: text.length * size * 0.5 };
        },
      };
      return element.context;
    },
    remove() {
      const at = element.parent?.children.indexOf(element) ?? -1;
      if (at >= 0) {
        element.parent.children.splice(at, 1);
      }
    },
    removeAttribute(name) {
      delete element.attributes[name];
    },
    replaceChildren(...nodes) {
      element.children = [];
      element.append(...nodes);
    },
    setAttribute(name, value) {
      element.attributes[name] = String(value);
    },
  };
  element.classList = { add() {}, contains: () => false, remove() {}, toggle: () => false };
  element.dataset = {};
  return element;
}

/** Install the shim and load the vendored build against it, once for the suite. */
async function loadPdfjs() {
  globalThis.document ??= {
    body: shimElement("body"),
    createElement: shimElement,
    createElementNS: (_ns, tag) => shimElement(tag),
  };
  // What the build reaches for at load, as `pdfjs-build.test.mjs` does.
  globalThis.DOMMatrix ??= class {};
  globalThis.Path2D ??= class {};
  globalThis.ImageData ??= class {};
  const pdfjs = await import(new URL("pdf.min.mjs", VENDOR).href);
  pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdf.worker.min.mjs", VENDOR).href;
  return pdfjs;
}

/** The synthetic page, and a container the viewer's own call has filled with its text. */
async function renderedLayer({ rotation = 0, scale = 1 } = {}) {
  const pdfjs = await loadPdfjs();
  const task = pdfjs.getDocument({ data: new TextEncoder().encode(PDF), isEvalSupported: false, verbosity: 0 });
  const pdf = await task.promise;
  const page = await pdf.getPage(1);
  const viewport = page.getViewport({ rotation, scale });
  const container = shimElement("div");
  const layer = startTextLayer({ TextLayer: pdfjs.TextLayer, container, page, viewport });
  await layer.render();
  await task.destroy();
  return { container, layer, viewport };
}

test("text layer: the vendored TextLayer writes the page's text into the viewer's container", async () => {
  const { container, layer } = await renderedLayer({ scale: 1.5 });
  assert.deepEqual(layer.textContentItemsStr, ["Passage"]);
  assert.deepEqual(container.children.map((span) => span.textContent), ["Passage"]);
  const [span] = container.children;
  assert.equal(span.tagName, "SPAN");
  assert.equal(span.attributes.role, "presentation", "the text is the page's, not a control");
  // 20 pt from the left of a 200 pt page; the top is the baseline less the ascent.
  assert.equal(span.style.left, "10.00%");
  assert.match(span.style.top, /^4[0-9]\.\d\d%$/);
  assert.equal(span.style.getPropertyValue("--font-height"), "12.00px", "the font is in page units, scaled by CSS");
  assert.equal(container.style.getPropertyValue("--total-scale-factor"), "1.5");
  assert.equal(container.attributes["data-main-rotation"], "0");
});

test("text layer: the container is sized from the scale the viewer set, and turns with the page", async () => {
  const { container } = await renderedLayer({ rotation: 90, scale: 2 });
  assert.equal(container.style.width, "round(down, var(--total-scale-factor) * 200px, var(--scale-round-x))");
  assert.equal(container.style.height, "round(down, var(--total-scale-factor) * 200px, var(--scale-round-y))");
  assert.equal(container.style.getPropertyValue("--total-scale-factor"), "2");
  assert.equal(container.attributes["data-main-rotation"], "90",
               "the spans stay on the unrotated page; the stylesheet turns the layer");
});

test("text layer: a second call clears the container, and a cancelled layer stops rendering", async () => {
  const pdfjs = await loadPdfjs();
  const task = pdfjs.getDocument({ data: new TextEncoder().encode(PDF), isEvalSupported: false, verbosity: 0 });
  const pdf = await task.promise;
  const page = await pdf.getPage(1);
  const viewport = page.getViewport({ scale: 1 });
  const container = shimElement("div");
  const first = startTextLayer({ TextLayer: pdfjs.TextLayer, container, page, viewport });
  const pending = first.render();
  first.cancel();
  await assert.rejects(pending, /cancelled/i);
  const second = startTextLayer({ TextLayer: pdfjs.TextLayer, container, page, viewport });
  await second.render();
  assert.deepEqual(container.children.map((span) => span.textContent), ["Passage"],
                   "one page of text, never two layers' worth");
  assert.equal(container.attributes["data-main-rotation"], "0");
  await task.destroy();
});

test("text layer: the shell holds the container under the boxes, and viewer.css reads what the build writes", async () => {
  const css = await readFile(path.join(VIEWER_DIR, "viewer.css"), "utf8");
  const shell = await readFile(path.join(VIEWER_DIR, "index.html"), "utf8");
  assert.match(shell, /<div id="text-layer" class="text-layer"><\/div>/);
  // Between the canvas and the boxes: the boxes take the clicks, the text takes the selection.
  assert.match(shell, /<canvas id="pdf-canvas"[^>]*><\/canvas>\s*(<!--[\s\S]*?-->\s*)?<div id="text-layer"[\s\S]*?<svg/);
  assert.match(css, /\.overlay \{[^}]*pointer-events: none;/, "the SVG root must not take the pointer");
  assert.match(css, /\.overlay-region \{[^}]*pointer-events: visible;/, "a box still does");
  const { container } = await renderedLayer({ rotation: 0, scale: 1 });
  const [span] = container.children;
  const properties = [...Object.keys(container.style), ...Object.keys(span.style)].filter((key) => key.startsWith("--"));
  assert.ok(properties.includes("--font-height") && properties.includes("--min-font-size"),
            "the run has to set properties, or this test proves nothing");
  // A rule has to read them, not a comment about them.
  const rules = css.replace(/\/\*[\s\S]*?\*\//g, "");
  for (const property of properties) {
    if (property === "--min-font-size") {
      assert.ok(!rules.includes(property) && css.includes(property),
                "--min-font-size is the one value the rules leave alone, and viewer.css says why");
      continue;
    }
    assert.ok(rules.includes(property), `viewer.css says nothing about ${property}`);
  }
  for (const rotation of [0, 90, 180, 270]) {
    const { container: turned } = await renderedLayer({ rotation, scale: 1 });
    const written = turned.attributes["data-main-rotation"];
    assert.ok(rotation === 0 || css.includes(`[data-main-rotation="${written}"]`),
              `viewer.css does not turn a layer at ${written} degrees`);
  }
});
