"""Install TurboPython agent docs into a target project."""

import io
from contextlib import redirect_stdout
from pathlib import Path
import shutil

from . import get_docs_dir
from .dump_types import dump_builtin_types

# (source name in wheel/repo, target name in user project)
# In dev layout, get_docs_dir() resolves to the repo's docs/ which contains
# more files than we ship -- we iterate this explicit allowlist to avoid
# copying design docs.
_DOC_FILES: list[tuple[str, str]] = [
    ("TPY_FOR_AGENTS.md", "TPY_FOR_AGENTS.md"),
    ("LANGUAGE_FEATURES.md", "TPY_LANGUAGE_FEATURES.md"),
    ("STDLIB_ROADMAP.md", "TPY_STDLIB_ROADMAP.md"),
]

_API_REFERENCE_FILENAME = "TPY_API_REFERENCE.md"


def install_agent_docs(target_dir: Path) -> list[Path]:
    """Copy agent docs into target_dir, overwriting any existing copies.

    Creates target_dir if missing. Returns the list of written paths.
    Raises NotADirectoryError if target_dir exists as a regular file.
    """
    if target_dir.exists() and not target_dir.is_dir():
        raise NotADirectoryError(
            f"Cannot install agent docs into {target_dir}: "
            f"path exists and is not a directory"
        )
    target_dir.mkdir(parents=True, exist_ok=True)
    src = get_docs_dir()
    written: list[Path] = []
    for src_name, dst_name in _DOC_FILES:
        dst = target_dir / dst_name
        shutil.copyfile(src / src_name, dst)
        written.append(dst)

    # Generated reference: API surface reflected from the installed stubs.
    buf = io.StringIO()
    with redirect_stdout(buf):
        dump_builtin_types()
    dst = target_dir / _API_REFERENCE_FILENAME
    dst.write_text(buf.getvalue(), encoding="utf-8")
    written.append(dst)

    return written


def agents_md_snippet(target_dir: Path) -> str:
    """Return the copy-pasteable section to append to AGENTS.md/CLAUDE.md.

    target_dir should be the path as the user typed it (typically relative),
    so the snippet references paths that make sense in the user's project.
    """
    path_prefix = target_dir.as_posix().rstrip("/")
    return f"""\
## TurboPython (TPy) documentation

This project uses TurboPython (tpyc) to compile Python source to C++.
When writing or modifying `.py` files compiled by tpyc:

- Start with `{path_prefix}/TPY_FOR_AGENTS.md` -- concise bootstrap
  covering the Python-to-TPy delta, ownership rules, and idiomatic
  patterns. Read this before writing TPy code.
- Consult `{path_prefix}/TPY_LANGUAGE_FEATURES.md` for depth on specific
  features. Only sections marked **Working** are usable today.
- Check `{path_prefix}/TPY_STDLIB_ROADMAP.md` before using a Python stdlib
  module -- coverage is partial and some modules are missing or blocked.
- Look up concrete API surface in `{path_prefix}/TPY_API_REFERENCE.md` --
  auto-generated from the installed stubs (builtins, `tplib.*`, and
  bundled stdlib modules). Reflects exactly what's callable in this
  version, with signatures and methods.

These files are bundled from the installed tpyc toolchain. Refresh with
`tpyc --install-agent-docs {path_prefix}` after upgrading tpyc.
"""
