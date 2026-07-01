# Vendored third-party C/C++ libraries

Source trees for libraries TPy stdlib modules bind to (e.g. PCRE2 for `re`,
in the future zlib for `gzip`, sqlite3 for `sqlite3`, etc.). Each lib lives
in its own subdirectory and is built into TPy programs that import the
corresponding stdlib module, in `--<lib>=bundled` mode.

The vendored content is **committed** (not fetched on first build) so that
`tpyc -x program.py` works offline, with no setup step beyond `uv sync`,
and so that wheels built for PyPI distribution carry the sources directly.

## How vendoring is done

Each library has a corresponding `scripts/vendor_<name>.py`. The script
pins the upstream URL + SHA256 + version, downloads the release tarball,
extracts into this directory, and strips files we don't need. Re-running
the script reproduces the committed content exactly.

After vendoring, the script writes a sidecar metadata file
**next to** the vendored tree (not inside it) at
`runtime/cpp/third_party/<name>.vendor.json`. The sidecar records the
version + URL + SHA256 + vendored timestamp -- single place to look up
"what's currently vendored", and the upstream source tree stays pristine
(no risk of our marker colliding with a future upstream file). The
script reads the sidecar's `version` field for idempotency: re-running it
on an already-vendored tree is a no-op.

## Bumping a vendored lib

1. Edit `scripts/vendor_<name>.py`: bump `VERSION`, `URL`, `SHA256`.
2. Run: `uv run python scripts/vendor_<name>.py`.
3. Review the diff in `runtime/cpp/third_party/<name>/`.
4. If upstream added, removed, or renamed source files, update the
   corresponding sidecar source list (e.g.
   `runtime/cpp/third_party/<name>.sources.txt`).
5. If our manual mirror header
   (`runtime/cpp/include/tpy/stdlib/<name>_h.hpp`) needs updating because
   the upstream API changed shape, do that too.
6. Commit.

## Current contents

| Directory | Sidecar | Lib | Used by | Vendor script |
|---|---|---|---|---|
| `pcre2/` | `pcre2.vendor.json` | PCRE2 10.44 | `re` (via `_bindings.pcre2`) | `scripts/vendor_pcre2.py` |
| `mbedtls/` | `mbedtls.vendor.json` | mbedTLS 3.6.6 | `ssl` (via `_bindings.mbedtls`) | `scripts/vendor_mbedtls.py` |
| `cacert/` | `cacert.vendor.json` | Mozilla CA roots (certifi 2026.06.17) | `ssl` default trust store (compiled-in blob `cacert_data.c`, built with mbedTLS) | `scripts/vendor_cacert.py` |

## Why committed instead of fetched

- **No setup friction**: `git clone` + `uv sync` and you can build, no extra step.
- **Works offline**: airgapped dev environments, restricted CI runners, distro packagers all work.
- **Wheel-ready**: PyPI wheels include the sources directly, end users
  installing via `pip install tpyc` don't hit a build-time fetch.
- **Reproducibility**: the committed tree is the canonical thing tpyc tests
  against; the vendor script is a tool to reproduce that tree, not a
  build-time prerequisite.

The cost (a few MB per lib in git history) is paid once; the friction-of-
not-doing-it would be paid by every contributor and every CI run.
