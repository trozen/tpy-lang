"""Hatch build hook: ships tpyc/_buildinfo.py stamped with git metadata."""

import subprocess
import tempfile
from pathlib import Path
from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class BuildInfoHook(BuildHookInterface):
    PLUGIN_NAME = "buildinfo"

    def initialize(self, version, build_data):
        force_include = build_data.setdefault("force_include", {})
        shipped = Path(self.root) / "tpyc" / "_buildinfo.py"
        commit = _git_commit(self.root)
        # A wheel built from an unpacked sdist has no git metadata, but the
        # sdist already carries a stamped module: ship that one rather than
        # clobbering it with "unknown".
        if commit == "unknown" and shipped.exists():
            force_include[str(shipped)] = "tpyc/_buildinfo.py"
        else:
            # Stamped OUTSIDE the source tree. A tpy/tpyc run in the same
            # checkout fingerprints every .py under tpyc/ for its build cache,
            # so a file that appears and disappears there mid-build makes an
            # unchanged rerun miss the cache.
            self._stamp_dir = tempfile.TemporaryDirectory()
            stamp = Path(self._stamp_dir.name) / "_buildinfo.py"
            stamp.write_text(f'GIT_COMMIT = "{commit}"\n')
            force_include[str(stamp)] = "tpyc/_buildinfo.py"
        if version == "editable":
            # A copy of the CPython-side packages would go stale and shadow
            # lib/cpy, which the editable path entries already expose. An
            # unpacked sdist has no lib/cpy/tplib link (the sdist leaves it
            # out), so there tplib still needs its copy.
            linked = {"tpy"}
            if (Path(self.root) / "lib" / "cpy" / "tplib").exists():
                linked.add("tplib")
            editable = {src: dst for src, dst in self.build_config.force_include.items()
                        if dst.split("/", 1)[0] not in linked}
            editable.update(force_include)
            build_data["force_include_editable"] = editable

    def finalize(self, version, build_data, artifact_path):
        stamp_dir = getattr(self, "_stamp_dir", None)
        if stamp_dir is not None:
            stamp_dir.cleanup()


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
