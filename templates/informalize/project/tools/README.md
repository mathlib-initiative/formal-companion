# Project tools

Put project-specific exporters and maintenance commands here, with regression
tests in ../tests/. Add their checks to the root Makefile so both local
validation and publication run them.

Use the generated tools in informal/tools/ and pdf-overlay/tools/ for shared
behavior. Change those in formal-companion and regenerate them rather than
forking a copied runtime. See ../docs/maintenance.md for the build sequence.
