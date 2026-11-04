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
closure (`scope`).  `scripts/cfcmd/informalize.py` is the command.
"""
