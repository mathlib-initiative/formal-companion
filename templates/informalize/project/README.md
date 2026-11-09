# {{ title }}

An informal account of selected definitions, results, and proofs from
[{{ library_name }}]({{ library_url }}). The PDF connects the mathematical text
to pinned Lean declarations using
[Literature Links](https://github.com/mathlib-initiative/literature-links).

<!-- Add the published viewer and versioned PDF download links here at release. -->

## Repository guide

| Directory | Contents |
| --- | --- |
| [informal/](informal/) | LaTeX text, notation, and curated statement links |
| [annotations/](annotations/) | Exported Literature Links records, created with the overlay |
| [pdf-overlay/](pdf-overlay/) | Generated viewer, bundled annotations, and hover data |
| [tools/](tools/) | Project-specific tools |
| [tests/](tests/) | Repository checks and project regression tests |
| [docs/](docs/) | Build, regeneration, and publication instructions |

## Development

On Linux or macOS, use Python 3.11 or later, Git, and Make:

```sh
make check
make pdf
```

The PDF build requires TeX Live. After the overlay has been generated,
`make site` checks the repository, downloads and verifies the released PDF,
and builds the static website in `site/`. No Lean installation or Python
packages are needed to check the repository or build the website.

See [CONTRIBUTING.md](CONTRIBUTING.md) for editing guidance and
[the maintenance guide](docs/maintenance.md) for regeneration and publication.

## License

The text and PDF are released under {{ license_name }} ([LICENSE](LICENSE)).
The generated runtime retains its
[Apache-2.0 notice](informal/LICENSE-runtime); bundled viewer dependencies
carry their own notices.
