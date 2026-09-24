"""A stand-in `ssh` that runs the command locally, so the whole session --
probe, slot gate, rsync, setup, execnet workers -- can run against this
machine with no network. Options are skipped, the host is logged, and the
one variable the tests forward is unset so forwarding is really tested."""

import stat
import sys
import textwrap
from pathlib import Path

SCRIPT = """\
#!/usr/bin/env bash
while [ $# -gt 0 ]; do
  case "$1" in
    -F|-o|-l|-p|-i|-E) shift 2 ;;
    -*) shift ;;
    *) break ;;
  esac
done
host="$1"; shift
[ -n "$FAKE_SSH_LOG" ] && echo "$host" >> "$FAKE_SSH_LOG"
# a command containing FAKE_SSH_FAIL_MATCH fails, the way a host that
# answers the probe but breaks on a later command would
if [ -n "$FAKE_SSH_FAIL_MATCH" ] && [[ "$*" == *"$FAKE_SSH_FAIL_MATCH"* ]]; then
  echo "fake ssh: refusing: $FAKE_SSH_FAIL_MATCH" >&2
  exit 3
fi
# a real host inherits nothing from the controller: the forwarded env must
# arrive through the worker spec, so drop the variable the tests forward
unset DEMO_TOKEN
exec bash -c "$*"
"""


def install(bin_dir: Path) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    ssh = bin_dir / "ssh"
    ssh.write_text(SCRIPT)
    ssh.chmod(ssh.stat().st_mode | stat.S_IXUSR)
    return ssh


def venv_setup_command(scripts_dir: Path) -> str:
    """A "setup" that makes `<venv>/bin/python` run this interpreter, which
    has pytest and xdist installed, instead of building a real venv. A
    wrapper script rather than a symlink: python locates its venv from the
    path it was invoked by, and a symlink into a real venv would resolve
    past that venv to the base interpreter."""
    script = scripts_dir / "mkvenv.sh"
    script.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        set -e
        mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"
        printf 'from setup' > setup-wrote.txt   # in the tree: must not travel home
        printf '#!/bin/sh\\nexec {sys.executable} "$@"\\n' > "$UV_PROJECT_ENVIRONMENT/bin/python"
        chmod +x "$UV_PROJECT_ENVIRONMENT/bin/python"
        """))
    return f"sh {script}"
