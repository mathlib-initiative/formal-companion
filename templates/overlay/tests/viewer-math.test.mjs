import assert from "node:assert/strict";
import test from "node:test";
import { mathRuns, mathMarkup, mathNode } from "../runtime/viewer/viewer-math.js";

test("math labels: prose and explicit math stay separate", () => {
  assert.deepEqual(mathRuns(String.raw`Inclusion \(\iota_S:S\longrightarrow X\)`), [
    {kind:"text", text:"Inclusion "},
    {kind:"math", text:String.raw`\iota_S:S\longrightarrow X`, raw:String.raw`\(\iota_S:S\longrightarrow X\)`},
  ]);
  assert.deepEqual(mathRuns("$x_i$ and $y^2$").map(r => r.kind), ["math","text","math"]);
  assert.equal(mathRuns(String.raw`\[\frac{a}{b}\]`)[0].text, String.raw`\frac{a}{b}`);
  assert.equal(mathRuns("$$x+y$$")[0].text, "x+y");
  for (const text of ["Inclusion map", String.raw`Costs \$5`, String.raw`Unclosed \(x_i`, "file_name"]) {
    assert.deepEqual(mathRuns(text), [{kind:"text", text}]);
  }
});

test("math labels: actual LaTeX becomes native MathML", () => {
  const result = mathMarkup(String.raw`\iota_S:S\longrightarrow X`);
  assert.match(result, /<msub><mi>ι<\/mi><mi>S<\/mi><\/msub>/);
  assert.match(result, /<mo>⟶<\/mo>/);
  assert.match(mathMarkup(String.raw`\frac{a}{b}`), /<mfrac>/);
  assert.match(mathMarkup(String.raw`\gamma_{[r,s]}`), /<msub>/);
  assert.match(mathMarkup(String.raw`\Pi_1(\iota_{U_i})`), /<msub>[\s\S]*<msub>/);
});

test("math labels: errors are local and unsafe commands cannot create active content", () => {
  assert.equal(mathMarkup(String.raw`\unknownmacro{x}`), null);
  assert.equal(mathMarkup(String.raw`\frac{`), null);
  assert.equal(mathMarkup("x".repeat(4097)), null);
  assert.equal(mathMarkup(String.raw`\def\x{\x}\x`), null);
  for (const tex of [String.raw`\href{javascript:alert(1)}{x}`, String.raw`\includegraphics{https://example.invalid/x}`, String.raw`\htmlClass{evil}{x}`]) {
    const result = mathMarkup(tex) ?? "";
    assert.doesNotMatch(result, /<(?:a|script|img|iframe)\b|\shref=|\son\w+=/);
  }
  assert.equal(mathNode("x", {}), null, "no parser leaves a readable source fallback");
});
