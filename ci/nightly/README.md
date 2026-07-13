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

- The 24.04 clang row is clang-19 (from the updates archive), NOT the distro
  default clang-18: clang 18 defines `__cpp_concepts` as 201907, which keeps
  libstdc++'s `<expected>` disabled, so it cannot build the TPy runtime with
  libstdc++ at all (clang 19 bumped it to 202002).
- The system-deps row exercises `--dep-mode pcre2=system,mbedtls=system`
  against the distro's libpcre2-dev/libmbedtls-dev.

## Caching (deliberate -- do not "optimize")

A named docker volume `tpy-nightly-cache` (auto-created on first run) is
mounted at `/cache` in every container and shared across configs and nights:
`/cache/tpyc` (stdlib .o + PCH, content-addressed per toolchain),
`/cache/ccache`, `/cache/uv`. These are compile-reuse caches -- every case
still compiles+links+runs, so sharing them cannot mask a failure. The
result-skip cache (`exec-results/`) also lands in the volume but is
neutralized by `--force-exec` on every run: skipping unchanged cases is
exactly what a breakage-catching nightly must not do, and a marker's key may
not capture every environmental input (a libstdc++ point update, a
system-lib bump on the system-deps config).

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
   0 1 * * * $HOME/tpy-nightly/repo/ci/nightly/cron-nightly.sh >> $HOME/tpy-nightly/cron.log 2>&1
   ```

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
- A macOS cross-compile config (osxcross, build-only) is a planned second
  phase, NOT built yet: it needs a `--build-only` harness mode and
  committed-baseline/delta tooling for the mac fail-set first.
