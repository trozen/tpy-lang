# Vendored third-party C/C++ libraries

Source trees for libraries TPy stdlib modules bind to (e.g. PCRE2 for `re`,
zlib for `zlib`/`gzip`, in the future sqlite3 for `sqlite3`, etc.). Each lib lives
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
apart from any `<name>.patches/` backports the vendor script applies
(no risk of our marker colliding with a future upstream file). The
script reads the sidecar's `version` field for idempotency: re-running it
on an already-vendored tree is a no-op.

## Bumping a vendored lib

1. Edit `scripts/vendor_<name>.py`: bump `VERSION`, `URL`, `SHA256`.
2. If `runtime/cpp/third_party/<name>.patches/` exists (local backports of
   not-yet-released upstream fixes, applied by the vendor script after
   extraction and listed in the sidecar's `patches` field), DROP every
   patch the new release already contains -- each patch's header names its
   upstream commits and when to drop it. A kept patch that no longer
   applies makes the script fail loudly rather than skip.
3. Run: `uv run python scripts/vendor_<name>.py`.
4. Review the diff in `runtime/cpp/third_party/<name>/`.
5. If upstream added, removed, or renamed source files, update the
   corresponding sidecar source list (e.g.
   `runtime/cpp/third_party/<name>.sources.txt`).
6. If our manual mirror header
   (`runtime/cpp/include/tpy/stdlib/<name>_h.hpp`) needs updating because
   the upstream API changed shape, do that too.
7. Commit.

## Current contents

| Directory | Sidecar | Lib | Used by | Vendor script |
|---|---|---|---|---|
| `pcre2/` | `pcre2.vendor.json` | PCRE2 10.44 | `re` (via `_bindings.pcre2`) | `scripts/vendor_pcre2.py` |
| `mbedtls/` | `mbedtls.vendor.json` | mbedTLS 3.6.6 | `ssl` (via `_bindings.mbedtls`) | `scripts/vendor_mbedtls.py` |
| `cacert/` | `cacert.vendor.json` | Mozilla CA roots (certifi 2026.06.17) | `ssl` default trust store (compiled-in blob `cacert_data.c`, built with mbedTLS) | `scripts/vendor_cacert.py` |
| `date/` | `date.vendor.json` | Howard Hinnant date 3.0.4 | `datetime` tz backend (via `_bindings.hinnant_date`; `tz.cpp` built with `USE_OS_TZDB`, TPy-facing surface is the hand-written `tpy/stdlib/datetime.hpp` facade) | `scripts/vendor_date.py` |
| `zlib/` | `zlib.vendor.json` | zlib 1.3.2 (core library, no gzFile API) | `zlib` and `gzip` (via `_bindings.zlib` + the `zlib_shim.c` glue) | `scripts/vendor_zlib.py` |

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
