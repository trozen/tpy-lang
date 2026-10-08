#!/usr/bin/env python3
"""GitHub Actions side of the nightly (.github/workflows/nightly.yml).

  gha.py matrix [--rows a,b] [--smoke]
      configs.json -> the job matrix, one JSON object on stdout:
      {"linux": [row, ...], "macos": [row, ...]}
  gha.py report <results-dir> --matrix <json> [--smoke] [--rev <sha>]
      the rows' junit + exit-code files -> the report (markdown) on stdout;
      exit 1 unless every row is green.

The rows run the same phases as the local runner (nightly.py), from the same
configs.json: a row's pytest arguments come from `suite_pytest_args`.
Stdlib-only.
"""

import argparse
import json
import shlex
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nightly  # noqa: E402

CONFIGS = Path(__file__).resolve().parent / "configs.json"
LINUX_BASE_PKGS = "ca-certificates git curl python3 python3-venv"
MACOS_RUNNER = "macos-15"


def matrix(configs: list[dict], rows: set[str], smoke: bool) -> dict:
    linux, macos = [], []
    for cfg in configs:
        name = cfg["name"]
        if rows and name not in rows:
            continue
        # A host-installed toolchain (osxcross) does not exist on a hosted
        # runner; the native macOS job covers that platform instead.
        if cfg.get("requires") or cfg.get("mounts"):
            continue
        junit = f"junit-{name}.xml"
        if cfg.get("platform") == "macos":
            # GitHub's macOS image also ships Homebrew GCC, which toolchain
            # auto-detection would pick over the Apple clang macOS builds target.
            args = nightly.native_pytest_args(smoke, junit, cxx="clang")
            macos.append({"name": name, "runner": MACOS_RUNNER, "script": False,
                          "command": f"uv run pytest {shlex.join(args)}"})
            continue
        if cfg.get("script"):
            command = cfg["script"]
        else:
            command = ("uv run pytest "
                       + shlex.join(nightly.suite_pytest_args(cfg, smoke, junit)))
        pkgs = [LINUX_BASE_PKGS]
        if not cfg.get("cpython"):
            pkgs.append("lld")  # the harness links with lld when it works
        pkgs += [cfg.get("toolchain_pkgs", ""), cfg.get("extra_pkgs", "")]
        if cfg.get("cpython"):
            env = {"UV_PYTHON": cfg["cpython"], "UV_PYTHON_DOWNLOADS": "automatic"}
        else:
            # The distro Python is the base image's axis -- never a managed one.
            env = {"UV_PYTHON": "/usr/bin/python3", "UV_PYTHON_DOWNLOADS": "never"}
        linux.append({
            "name": name,
            "container": cfg["base_image"],
            "apt": " ".join(p for p in pkgs if p),
            "uv_sync": " ".join(f"--extra {e}" for e in cfg.get("uv_extras", [])),
            "env": "\n".join([f"{k}={v}" for k, v in env.items()]
                             + cfg.get("env", [])),
            "script": bool(cfg.get("script")),
            "command": command,
        })
    return {"linux": linux, "macos": macos}


def _suite_seconds(junit: Path) -> float:
    try:
        return sum(float(s.get("time", 0))
                   for s in ET.parse(junit).getroot().iter("testsuite"))
    except (OSError, ET.ParseError):
        return 0.0


def row_result(results_dir: Path, name: str, script: bool) -> nightly.ConfigResult:
    res = nightly.ConfigResult(name=name)
    res.log_path = f"artifact nightly-{name} (suite-{name}.log)"
    junit = results_dir / f"junit-{name}.xml"
    exit_file = results_dir / f"exit-{name}.txt"
    rc = int(exit_file.read_text().strip()) if exit_file.exists() else None
    nightly.suite_verdict(res, junit, rc, script)
    res.duration_s = _suite_seconds(junit)
    return res


def report(results_dir: Path, rows: dict, smoke: bool, rev: str) -> tuple[str, bool]:
    results = [row_result(results_dir, r["name"], r["script"])
               for r in rows["linux"] + rows["macos"]]
    subject, body = nightly.format_report(results, rev, smoke, pull_failed=False)
    # A run that tested nothing is not a green night.
    return f"## {subject}\n\n```\n{body}```\n", bool(results) and all(r.ok for r in results)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("matrix")
    m.add_argument("--rows", default="", help="comma-separated config names")
    m.add_argument("--smoke", action="store_true")
    r = sub.add_parser("report")
    r.add_argument("results_dir", type=Path)
    r.add_argument("--matrix", required=True, help="the matrix command's output")
    r.add_argument("--smoke", action="store_true")
    r.add_argument("--rev", default="")
    args = ap.parse_args()
    if args.cmd == "matrix":
        configs = json.loads(CONFIGS.read_text())["configs"]
        rows = {n.strip() for n in args.rows.split(",") if n.strip()}
        unknown = rows - {c["name"] for c in configs}
        if unknown:
            print(f"unknown config(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        out = matrix(configs, rows, args.smoke)
        skipped = rows - {r["name"] for r in out["linux"] + out["macos"]}
        if skipped:
            print(f"not runnable on GitHub Actions (host-installed toolchain): "
                  f"{', '.join(sorted(skipped))}", file=sys.stderr)
            return 2
        print(json.dumps(out))
        return 0
    text, green = report(args.results_dir, json.loads(args.matrix), args.smoke,
                         args.rev[:10])
    print(text)
    return 0 if green else 1


if __name__ == "__main__":
    sys.exit(main())
