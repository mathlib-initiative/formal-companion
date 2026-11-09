PYTHON ?= python3
PDF_BUILD_DIR ?= build/pdf

.PHONY: help check test site pdf

help:
	@printf '%s\n' \
	  'make check  Check the runtime, project tests, and any generated overlay' \
	  'make test   Run project regression tests' \
	  'make site   Check and build the website (requires the overlay)' \
	  'make pdf    Build a development PDF (requires TeX Live)'

check: test
	@if test -d pdf-overlay; then \
	  $(PYTHON) pdf-overlay/tools/check_generated.py; \
	else \
	  printf '%s\n' 'Source scaffold checked; generate the overlay before building the site.'; \
	fi

test:
	$(PYTHON) -B -m unittest discover -s tests -p 'test_*.py'

site: check
	@test -f pdf-overlay/tools/serve.py || { \
	  printf '%s\n' 'Generate the overlay with cf informalize overlay first.' >&2; exit 1; }
	$(PYTHON) pdf-overlay/tools/serve.py --build-only

pdf:
	bash informal/tools/build_pdf.sh "$(PDF_BUILD_DIR)"
