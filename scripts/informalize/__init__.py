"""The informalize mode: an informal companion written from a Lean library.

New code, for docs/proposals/informalize.md.  Component 3 of its section 11 lives
here: the settings file `informalize.yaml` (`config`), the output repository and the
one exception to "never write to a repository" it is written under (`outrepo`), the
runtime `cf informalize sync` copies into it (`runtime`), the project files and the
license `init` renders once (`project`), the item table's rules and `check-items`
(`itemtable`), stage receipts and approvals (`stages`) and the maintainer's gates
(`approve`).  `scripts/cfcmd/informalize.py` is the command.
"""
