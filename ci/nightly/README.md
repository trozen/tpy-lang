# Nightly CI

Nightly all-builds CI for the Linux config matrix. Varied build
configurations (compiler versions, dependency modes, distro/Python/libstdc++
combinations) break in ways a single local dev toolchain never surfaces;
this nightly is the safety net that catches toolchain/config breakage.
Correctness of the code itself is covered by the developer running the suite
before pushing. Runs as a plain cron job on the dedicated test box -- one
fresh container per config, sequentially (a full cold config is ~25 min on
16 cores, so the whole matrix fits a night without parallelism).

## Files

- `configs.json` -- the config matrix (base image, compiler packages, `--cxx`
  toolchain, extra pytest args). Adding a config = one JSON entry.
- `Dockerfile` -- one parameterized image for every config
  (`BASE_IMAGE` / `TOOLCHAIN_PKGS` / `EXTRA_PKGS` build args). The zig config
  installs no compiler packages; zig comes from the `ziglang` wheel
  (`uv sync --extra bundled`, pinned by `uv.lock`).
- `nightly.py` -- host-side orchestrator (stdlib-only python3): builds images,
  runs one fresh container per config sequentially, parses junit results,
  emails the report via msmtp, prunes old logs.
- `cron-nightly.sh` -- the single script cron invokes: flock, source the env
  file, `git pull` master, exec the freshly-pulled `nightly.py`.

## Config notes

- The 24.04 clang rows are clang-19 and clang-20 (both official updates
  archive), NOT the distro default clang-18: clang 18 defines
  `__cpp_concepts` as 201907, which keeps libstdc++'s `<expected>` disabled,
  so it cannot build the TPy runtime with libstdc++ at all (clang 19 bumped
  it to 202002). clang-19 guards the supported floor; clang-20 is the
  highest 24.04 clang, i.e. what `--cxx clang` (best-versioned) actually
  resolves to on an updated box with it installed.
- The 26.04 rows pin the release's shipped defaults (g++-15, clang-21,
  observed 2026-07) rather than the `g++`/`clang` metapackages: a mid-LTS
  default bump would silently re-key every cache and shift results overnight
  with no code change. Re-pin deliberately when moving to a newer base.
- The `cpy-3.12` / `cpy-3.13` / `cpy-3.14` rows are the CPython-version
  axis, kept SEPARATE from the C++-toolchain axis. CPython output is
  toolchain-independent and the C++ build is Python-version-independent, so
  each runs `--no-exec` (comp + cpy, no C++ build -- the cheapest rows in
  the matrix) with `UV_PYTHON` pinned to that version (a managed download,
  not the base image's distro `python3`), making parity coverage
  deterministic instead of implicitly inherited from whatever Python a base
  image happens to ship. Correspondingly the C++-toolchain rows now run
  `--no-cpy`: they own compile+build+run correctness, this axis owns CPython
  parity, and neither re-does the other's work. Add a version by copying a
  row and bumping `cpython`; its `g++-13` only satisfies `--cxx` / the cache
  prewarm and is never invoked under `--no-exec`.
- The system-deps row exercises `--dep-mode pcre2=system,mbedtls=system`
  against the distro's libpcre2-dev/libmbedtls-dev. Ubuntu 24.04 ships
  mbedTLS 2.28 (the mainstream-LTS branch); the ssl shim supports it
  alongside 3.x via a single version `#ifdef`, so the system floor is
  2.28.0. Anything older is rejected cleanly by the version guard
  (`SystemLibVersionError`) rather than failing deep in the C compiler.
- The macos-arm64 row cross-compiles the whole case set with osxcross and
  passes nothing but `--cxx`: the harness detects the darwin target itself
  (`-dumpmachine`) and auto-degrades exec to build-only. The row
  self-disables ("unavailable" in the report, never red) until the
  `requires` paths exist -- one-time setup:

  ```
  # on a Mac (Xcode or CLT installed):
  git clone https://github.com/tpoechtrager/osxcross && cd osxcross
  ./tools/gen_sdk_package.sh        # CLT-only: gen_sdk_package_tools.sh
  # copy the MacOSX*.sdk.tar.xz to the box, then on the box:
  git clone https://github.com/tpoechtrager/osxcross ~/tpy-nightly/osxcross
  cp MacOSX*.sdk.tar.xz ~/tpy-nightly/osxcross/tarballs/
  cd ~/tpy-nightly/osxcross && UNATTENDED=1 ./build.sh
  ```

  Two extra wrapper links pin the driver version (the wrapper parses the
  compiler name -- including a version suffix -- from its own filename, by
  design; the plain `clang` on PATH is 18, which cannot compile this SDK's
  libc++). The second is the C driver: tpyc derives it from the C++
  compiler name to build bundled C dependencies (PCRE2):

  ```
  cd ~/tpy-nightly/osxcross/target/bin
  ln -s arm64-apple-darwin25.5-wrapper arm64-apple-darwin25.5-clang++-19
  ln -s arm64-apple-darwin25.5-wrapper arm64-apple-darwin25.5-clang-19
  ```

  The row then activates by itself (it checks `requires` for BOTH
  versioned wrappers -- so a box with only `clang++-19` stays
  "unavailable" rather than going red on the C-dependency cases -- and
  mounts `target/` read-only at `/opt/osxcross`); the wrapper resolves
  `clang++-19`/`clang-19` from PATH at runtime, which the image's
  `clang-19` package provides. No deployment-target or linker setup is
  needed beyond these links: tpyc's toolchain layer detects the darwin
  target (`-dumpmachine`) and adjusts the command itself, so a manual
  `--cxx=<wrapper>` run and the CI behave identically. It pins
  `-mmacosx-version-min` (see `MACOS_VERSION_MIN` in `tpyc/toolchain.py`),
  overriding the wrapper's ancient default (11.0, which fails libc++
  availability checks for float `std::to_chars`), and points the link at
  the toolchain's own Mach-O linker (`<triple>-ld`, shipped alongside the
  wrapper) via `--ld-path` so it doesn't fall through to the host's ELF
  `ld`. Any case that does not build on mac is a genuine bug to fix (or,
  if truly unportable, gets a targeted skip added at that point); the row
  reports the fail-set as red -- that red is the worklist, not noise.

## Caching (deliberate -- do not "optimize")

A named docker volume `tpy-nightly-cache` (auto-created on first run) is
mounted at `/cache` in every container and shared across configs and nights:
`/cache/tpyc` (stdlib .o + PCH, content-addressed per toolchain),
`/cache/ccache`, `/cache/uv`. These are compile-reuse caches -- on the
C++-toolchain rows every case still compiles+links+runs, so sharing them
cannot mask a failure. Those rows pass `--force-exec`, which neutralizes the
result-skip cache (`exec-results/`) that also lands in the volume: skipping
unchanged cases is exactly what a breakage-catching nightly must not do, and
a marker's key may not capture every environmental input (a libstdc++ point
update, a system-lib bump on the system-deps config). The CPython-version
rows instead pass `--no-exec` (comp + cpy, no C++ build), so the exec cache
never applies to them.

## One-time box setup

1. **Docker**: install docker engine; add the nightly user to the `docker`
   group.
2. **Dedicated checkout** (never used for development -- the wrapper
   `git pull`s it):

   ```
   mkdir -p ~/tpy-nightly && cd ~/tpy-nightly
   git clone <repo-url> repo
   ```

3. **msmtp**: install `msmtp` (`apt install msmtp`; say yes to AppArmor
   support), then write `~/.msmtprc` (mode 600) for the SMTP account:

   ```
   defaults
   auth on
   tls on
   tls_trust_file /etc/ssl/certs/ca-certificates.crt
   logfile ~/.msmtp.log

   account default
   host <smtp-host>
   port 587
   from <sender-address>
   user <smtp-user>
   password <smtp-password>
   ```

   The logfile must stay at `~/.msmtp.log`: the AppArmor profile denies
   writes to other locations.

   Legacy-server wrinkles (both hit on the current box, 2026-07): a server
   cert from an internal CA is best pinned via `tls_fingerprint` (get it
   from `msmtp --serverinfo --tls --tls-certcheck=off --host=... --port=587`;
   re-pin on cert rotation). A server that only speaks TLS 1.0/1.1 cannot be
   reached at all under Ubuntu's system-wide GnuTLS policy -- priority
   strings can't re-enable disabled versions; the env file (step 4) must add
   `export GNUTLS_SYSTEM_PRIORITY_FILE=/dev/null`, which relaxes the policy
   only for processes run with that env. Prefer fixing the server (TLS 1.2)
   when possible.

   Verify: `echo test | msmtp <you>@<domain>`.

4. **Env file** `~/.config/tpy-nightly/env` (sourced by the wrapper):

   ```
   export TPY_NIGHTLY_EMAIL_TO=<you>@<domain>
   # export TPY_NIGHTLY_EMAIL_FROM=<sender>          # default: EMAIL_TO
   # export TPY_NIGHTLY_GREEN_EMAIL=always           # always|weekly|never
   # export TPY_NIGHTLY_LOGS=$HOME/tpy-nightly/logs
   # export TPY_NIGHTLY_CONFIG_TIMEOUT=10800
   ```

   Green emails default to every run: an expected green that does not arrive
   detects a silent cron death.

5. **Smoke-test the pipeline** (builds all images, runs a tiny `-k` slice per
   config, prints the report instead of emailing -- minutes, not hours):

   ```
   ~/tpy-nightly/repo/ci/nightly/cron-nightly.sh --smoke --no-email
   ```

   Then once with email to verify msmtp: `... --smoke`.

6. **Cron entry** (`crontab -e`):

   ```
   0 1 * * * $HOME/tpy-nightly/repo/ci/nightly/cron-nightly.sh
   ```

   The wrapper self-logs: without a terminal it appends its own output to
   `~/tpy-nightly/cron.log` (interactive runs print normally).

## Operation notes

- Logs: `~/tpy-nightly/logs/<YYYY-MM-DD_HHMM>/` -- per-config `<name>.log`
  (docker build + full pytest output), `junit-<name>.xml`, and `report.txt`
  (the emailed body). Newest 14 runs are kept.
- Failure emails list failing test names per config plus the log path, and
  are sent on every failing night (no dedup -- a regression nags until fixed).
- Base images + apt layers refresh weekly (`--pull --no-cache`); force with
  `nightly.py --refresh-images`.
- Run a subset: `nightly.py --configs 2404-gcc13,zig --no-email`.
- Exit code: 0 all green, 1 otherwise (visible in `cron.log`).
