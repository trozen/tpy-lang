"""End-to-end test of the whole-run build cache through the real CLI.

Each step runs `python -m tpyc` as a subprocess (the warm path os.execv()s
the binary, which would replace an in-process test runner). One sequenced
test keeps it to a handful of real C++ builds (ccache-warm after the first).
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import exec_is_cross

# The ALL_CAPS tuple-unpack with a negative literal defeats the automatic
# Final inference and warns; the warning exercises warm-path diagnostic
# replay.
PROG_V1 = """\
import math

X_LIMIT, X_FLOOR = 2.5, -1.0

def main() -> None:
    print("v1", math.floor(X_LIMIT + X_FLOOR + 1.0))

main()
"""


def run_tpyc(cwd: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tpyc", *argv],
        cwd=cwd, capture_output=True, text=True, timeout=600,
    )


def built_cold(r: subprocess.CompletedProcess) -> bool:
    """A cold run prints per-phase progress; a warm one prints nothing."""
    return "analyzed" in r.stderr


def test_build_cache_lifecycle(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    # Every step runs the built binary (`-x` build+run, warm-path os.execv,
    # program output, arg forwarding), so there is no build-only variant: a
    # cross toolchain emits a non-native binary that cannot execute here.
    if exec_is_cross() or request.config.getoption("--build-only"):
        pytest.skip("build-cache lifecycle needs a host-runnable binary")

    prog = tmp_path / "prog.py"
    prog.write_text(PROG_V1)

    # Cold build + run. The ALL_CAPS-without-Final warning must be recorded.
    r = run_tpyc(tmp_path, "prog.py", "-x")
    assert r.returncode == 0, r.stderr
    assert built_cold(r)
    assert "v1 2" in r.stdout
    assert "X_LIMIT" in r.stderr  # the warning

    manifest = tmp_path / "__tpyc__" / "prog.d" / "debug" / "build-manifest.json"
    assert manifest.is_file()
    data = json.loads(manifest.read_text())
    recorded = {Path(f["path"]).name for f in data["files"]}
    assert "prog.py" in recorded
    assert "math.py" in recorded  # stdlib module source is a tracked input

    # Warm rerun: no pipeline output, same program output, warning replayed.
    r = run_tpyc(tmp_path, "prog.py", "-x")
    assert r.returncode == 0, r.stderr
    assert not built_cold(r)
    assert "v1 2" in r.stdout
    assert "X_LIMIT" in r.stderr

    # Source edit -> rebuild with the new behavior.
    prog.write_text(PROG_V1.replace("v1", "v2"))
    r = run_tpyc(tmp_path, "prog.py", "-x")
    assert r.returncode == 0, r.stderr
    assert built_cold(r)
    assert "v2 2" in r.stdout

    # A new file shadowing stdlib `math` must invalidate (resolution replay).
    (tmp_path / "math.py").write_text(
        "def floor(x: float) -> int:\n    return 99\n")
    r = run_tpyc(tmp_path, "prog.py", "-x")
    assert r.returncode == 0, r.stderr
    assert built_cold(r)
    assert "v2 99" in r.stdout
    (tmp_path / "math.py").unlink()

    # Back to stdlib math (another rebuild), then confirm warm again.
    r = run_tpyc(tmp_path, "prog.py", "-x")
    assert built_cold(r)
    r = run_tpyc(tmp_path, "prog.py", "-x")
    assert not built_cold(r)
    assert "v2 2" in r.stdout

    # --rebuild bypasses a valid cache...
    r = run_tpyc(tmp_path, "prog.py", "-x", "--rebuild")
    assert r.returncode == 0, r.stderr
    assert built_cold(r)

    # ...but still records a fresh manifest, so the NEXT plain run is warm
    # (regression: --rebuild used to skip recording, forcing an extra cold
    # rebuild afterwards).
    r = run_tpyc(tmp_path, "prog.py", "-x")
    assert r.returncode == 0, r.stderr
    assert not built_cold(r)

    # The warm path must not import the compiler machinery -- that is the
    # feature's core performance property. A -b warm hit returns in-process
    # (no execv), so the subprocess can assert on its own sys.modules.
    guard = (
        "import sys\n"
        "sys.argv = ['tpyc', 'prog.py', '-b']\n"
        "from tpyc.cli import main_tpyc\n"
        "rc = main_tpyc()\n"
        "assert rc == 0, rc\n"
        "heavy = [m for m in ('tpyc.compiler', 'tpyc.parse', 'tpyc.sema',\n"
        "                     'tpyc.codegen_cpp', 'tpyc.typesys')\n"
        "         if m in sys.modules]\n"
        "assert not heavy, f'warm path imported heavy modules: {heavy}'\n"
    )
    r = subprocess.run([sys.executable, "-c", guard], cwd=tmp_path,
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr

    # Deleted binary -> cold rebuild even with a fresh manifest.
    (tmp_path / "__tpyc__" / "prog.d" / "debug" / "prog").unlink()
    r = run_tpyc(tmp_path, "prog.py", "-x")
    assert r.returncode == 0, r.stderr
    assert built_cold(r)

    # Build-only warm hit reports the binary without rebuilding.
    r = run_tpyc(tmp_path, "prog.py", "-b")
    assert not built_cold(r)
    assert r.stdout.startswith("Built: ")

    # A macro module is a compile-time input executed under CPython: its
    # file must be a tracked input, so editing it invalidates the cache
    # AND the new expansion takes effect.
    (tmp_path / "tagmac.py").write_text(
        "# tpy: macro_module\n"
        "from tpyc.macro_api import ClassInfo, class_macro\n"
        "\n"
        "\n"
        "@class_macro\n"
        "def tagged(cls: ClassInfo) -> None:\n"
        "    cls.add_method_from_source(\n"
        "        \"def tag(self) -> int:\\n    return 1\")\n")
    (tmp_path / "mprog.py").write_text(
        "from tagmac import tagged\n"
        "\n"
        "\n"
        "@tagged\n"
        "class Thing:\n"
        "    x: int\n"
        "\n"
        "    def __init__(self, x: int) -> None:\n"
        "        self.x = x\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    t = Thing(5)\n"
        "    print(\"tag\", t.tag())\n"
        "\n"
        "\n"
        "main()\n")
    r = run_tpyc(tmp_path, "mprog.py", "-x")
    assert r.returncode == 0, r.stderr
    assert built_cold(r)
    assert "tag 1" in r.stdout
    r = run_tpyc(tmp_path, "mprog.py", "-x")
    assert not built_cold(r)
    assert "tag 1" in r.stdout
    macro_src = (tmp_path / "tagmac.py").read_text()
    (tmp_path / "tagmac.py").write_text(macro_src.replace("return 1",
                                                          "return 2"))
    r = run_tpyc(tmp_path, "mprog.py", "-x")
    assert r.returncode == 0, r.stderr
    assert built_cold(r)
    assert "tag 2" in r.stdout

    # Script args are forwarded by the warm exec path.
    argy = tmp_path / "argy.py"
    argy.write_text("import sys\n\n"
                    "def main() -> None:\n    print(sys.argv[1])\n\n"
                    "main()\n")
    r = run_tpyc(tmp_path, "argy.py", "-x", "--", "first")
    assert r.returncode == 0, r.stderr
    assert "first" in r.stdout
    r = run_tpyc(tmp_path, "argy.py", "-x", "--", "second")
    assert not built_cold(r)
    assert "second" in r.stdout
