#!/usr/bin/env bash
# Single cron entry point for the nightly CI (see README.md).
# Serializes itself via flock, sources the local env file (email config),
# pulls master, then execs the freshly-pulled orchestrator -- so both this
# wrapper's caller and nightly.py always run the just-pulled revision.
#
# Everything lives inside main() so bash reads the whole script before the
# git pull can rewrite the file under it (bash re-reads scripts lazily).

main() {
    set -uo pipefail

    # Under cron (no tty), self-log: append everything to the cron log so
    # the crontab entry stays a bare script path. Interactive runs keep
    # printing to the terminal. Gated to the pre-lock parent -- the flock
    # child inherits the redirected fds and must not re-add a header.
    if [ ! -t 1 ] && [ "${TPY_NIGHTLY_LOCKED:-}" != 1 ]; then
        mkdir -p "$HOME/tpy-nightly"
        exec >>"$HOME/tpy-nightly/cron.log" 2>&1
        echo "=== $(date) cron-nightly"
    fi

    # Re-run under an exclusive lock: overlapping nightlies would fight over
    # CPU and the docker cache volume. Lock lives in $HOME (a /tmp path is
    # squattable by other local users), and a skipped overlap says so in
    # cron.log instead of exiting silently.
    if [ "${TPY_NIGHTLY_LOCKED:-}" != 1 ]; then
        export TPY_NIGHTLY_LOCKED=1
        flock -n -E 75 "$HOME/.tpy-nightly.lock" "$0" "$@"
        rc=$?
        if [ "$rc" -eq 75 ]; then
            echo "tpy-nightly: previous run still holds the lock; skipping"
        fi
        exit "$rc"
    fi

    # Machine-local config (TPY_NIGHTLY_EMAIL_TO etc.) -- see README.md.
    # shellcheck disable=SC1091
    [ -f "$HOME/.config/tpy-nightly/env" ] && . "$HOME/.config/tpy-nightly/env"

    local repo
    repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    cd "$repo" || exit 1

    if ! git checkout --quiet master || ! git pull --ff-only --quiet origin master; then
        # Keep going on a stale checkout: nightly.py flags it in the email,
        # which beats a silent no-run.
        export TPY_NIGHTLY_PULL_FAILED=1
    fi

    exec python3 ci/nightly/nightly.py "$@"
}

main "$@"
