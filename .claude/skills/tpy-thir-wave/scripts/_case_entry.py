"""Shared entry-source resolver for the probe scripts.

A frontend-plugin case (pascal/...) has no `src/main.py` -- its entry is
`src/main.<plugin-ext>`. Scripts that spelled `src/main.py` inline reported
those cases as `<unprobed>`, which reads as "nothing to see" and hid three
one-arm flips in one wave. Reuse conftest's own discovery rules so the probe
picks exactly the file the test harness compiles.

Import from a script that has already put `tests/` on `sys.path`:

    from _case_entry import entry_src
"""
from __future__ import annotations

from pathlib import Path

import conftest as C


def entry_src(case_dir: Path) -> Path | None:
    """The entry source the harness would compile for `case_dir`, or None
    when the case has no recognizable source (callers decide what to print)."""
    main_src = case_dir / "src" / "main.py"
    if main_src.exists():
        return main_src
    src_dir = case_dir / "src"
    if not src_dir.is_dir():
        return None
    exts = C._all_plugin_entry_extensions()
    src_files: list[Path] = []
    for ext in exts:
        src_files.extend(src_dir.glob(f"*{ext}"))
    if not src_files:
        return None
    return C._pick_main_src(src_files, [e for e in exts if e != ".py"])
