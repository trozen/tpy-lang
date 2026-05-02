"""Hatch build hook: generates tpyc/_buildinfo.py with git metadata."""

import subprocess
from pathlib import Path
from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class BuildInfoHook(BuildHookInterface):
    PLUGIN_NAME = "buildinfo"

    def initialize(self, version, build_data):
        out = Path(self.root) / "tpyc" / "_buildinfo.py"
        commit = _git_commit(self.root)
        # When building wheel from an unpacked sdist, the temp dir isn't a
        # git repo, so `git describe` fails. The sdist already carries a
        # stamped _buildinfo.py; keep it instead of clobbering with "unknown".
        if commit == "unknown" and out.exists():
            return
        out.write_text(f'GIT_COMMIT = "{commit}"\n')

    def finalize(self, version, build_data, artifact_path):
        out = Path(self.root) / "tpyc" / "_buildinfo.py"
        out.unlink(missing_ok=True)


def _git_commit(root: str) -> str:
    try:
        r = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=root, capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return "unknown"
