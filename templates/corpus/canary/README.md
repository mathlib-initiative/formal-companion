# The probe's acceptance canary

docs/proposals/wikipedia-locate.md §2.4: before any library on a toolchain is
ingested, the probe must classify four declarations of a scratch library
correctly on that toolchain. This directory is that library as a template; it
is rendered, built and probed **on the run host** (`cf` never builds a library),
and its index is checked by `cf corpus ingest <universe> --canary --from <dir>`,
which records the result in `universe.yaml` under `canaries`.

| declaration | what it is | must be decided as |
|---|---|---|
| `CfCanary.hiddenSorry` | a public theorem whose proof body — private under the module system — rests on `sorry` | `statement_only` |
| `CfCanary.wantedTheorem` | a theorem whose type is a `…Wanted` wrapper | `wanted` |
| `CfCanary.propDef` | a `Prop`-valued definition | `definition`, statement flag set |
| `CfCanary.axiomTheorem` | a theorem resting on a non-standard axiom | `axiom_dependent` |

Placeholders: `@@TOOLCHAIN@@` (the `lean-toolchain` line, e.g.
`leanprover/lean4:v4.34.0`), `@@MODULE_HEADER@@` and `@@MODULE_FOOTER@@` (for a
toolchain with the module system, `module` + `public section` and `end`;
empty for one without it, where the first case is an ordinary theorem and still
must be `statement_only`). `cf corpus probe-command <universe> --canary
--toolchain <toolchain>` prints the commands. These sources have not been built
in the skill's own test suite, which has no Lean; the tests check what ingest
decides from a probe's records of them.

The canary cannot catch everything §2.4 asks for: its `wantedTheorem` uses a
stand-in wrapper the probe always sees, while a real `proof_wanted` that adds no
constant needs the source scan of rule 1, which is not implemented — so such a
result is absent from every index whatever the canary says
(`references/locate-documents.md` §8).
