"""The informalize mode: an informal companion written from a Lean library.

New code, for docs/proposals/informalize.md.  Component 3 of its section 11 lives
here: the settings file `informalize.yaml` (`config`), the output repository and the
one exception to "never write to a repository" it is written under (`outrepo`), the
runtime `cf informalize sync` copies into it (`runtime`), the project files and the
license `init` renders once (`project`), the item table's rules and `check-items`
(`itemtable`), stage receipts and approvals (`stages`) and the maintainer's gates
(`approve`).  Component 4 adds the Lean side: stage I1's two ends on the operator's
machine (`library`) and its host script with the helper it calls there
(`library_host.sh`, `library_host`, which import no cflib), and stage I2, the theorem's
closure (`scope`).  `scripts/cfcmd/informalize.py` is the command.  Component 5a adds
the fleet's planner (`plan`, behind `scripts/cfcmd/plan_informalize.py`), whose gate is
`scripts/gate/verify_informal.py`; the item table's rules it shares with that gate are the
runtime's `tools/fcitems.py`, which `itemtable` imports.  Component 5b adds `apply`, which
puts a harvested phase's files into the output repository; component 6 the canonical PDF's
two ends on the operator's machine (`pdf`, around the runtime's `tools/canonical_pdf.sh`
on the host); and component 7 the annotations, the generated site and its checks
(`overlay`), which run nowhere but here.
"""
