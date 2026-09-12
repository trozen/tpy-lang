"""Diagnostics name Python constructs, not the C++ mapping or the compiler's internals.

The user reads Python and may not read C++, and a second backend is planned, so a
committed diagnostic that says `std::string_view` or `TpyOrPattern` is a defect
(`docs/PITFALLS.md`, `no-cpp-in-diagnostics` / `no-internal-names-in-diagnostics`).
This walks every committed `diag.txt`, which is the population a user can actually
reach; a diagnostic string no case reaches is the reviewer's grep, not this test's.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DIAG_GLOBS = ("tests/cases/*/*/expected/diag.txt", "tests/interop/*/expected/diag.txt")

# Each token is a C++ spelling or a compiler-internal identifier that has leaked before,
# or would leak the same way. `int`/`str` alone are Python words, so the integer family
# is matched on its `_t` suffix and the node classes on their `Tpy` prefix.
FORBIDDEN = re.compile(
    r"std::|::tpy::|\btpy::|\bu?int(8|16|32|64)_t\b|string_view|std::variant|\bvariant<"
    r"|\b(unique_ptr|shared_ptr|make_unique|optional|vector|span|expected)<"
    r"|\.hpp\b|\bTpy[A-Z]\w+|\bnullptr\b|\bconstexpr\b|\bstatic_cast\b"
)

# A THIR reject ends its fixed prose with the lowering tag, e.g. "... not yet supported by
# C++ code generation (stmt.return:return.record_source.TpyCoerce.borrow)". The tag is the
# key the reject queue and its witness cases are organized by, and whether it belongs in
# user-facing text is an open decision (TODO.md), so the scan covers the prose and leaves
# the tag alone. Anchored on the prose so that no other trailing parenthetical is skipped.
REJECT_TAG = re.compile(r"(not yet supported by C\+\+ code generation) \([a-z_]+\.[\w.:]+\)$")

# The minimum number of diagnostic lines the walk must see. A path mapping that finds
# nothing would otherwise report a clean corpus; the real count is in the thousands.
MIN_DIAG_LINES = 1000


def _diag_files() -> list[Path]:
    files: list[Path] = []
    for pattern in DIAG_GLOBS:
        files.extend(ROOT.glob(pattern))
    return sorted(files)


def test_committed_diagnostics_name_python_constructs() -> None:
    offenders: list[str] = []
    seen = 0
    for path in _diag_files():
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if not line.strip():
                continue
            seen += 1
            match = FORBIDDEN.search(REJECT_TAG.sub(r"\1", line))
            if match:
                rel = path.relative_to(ROOT)
                offenders.append(f"{rel}:{lineno}: {match.group(0)!r} in: {line.strip()}")
    assert seen >= MIN_DIAG_LINES, f"walked only {seen} diagnostic lines; the glob is broken"
    assert not offenders, (
        f"{len(offenders)} committed diagnostic(s) name C++ or compiler internals "
        "(docs/PITFALLS.md no-cpp-in-diagnostics / no-internal-names-in-diagnostics):\n"
        + "\n".join(offenders)
    )


@pytest.mark.parametrize(
    "text",
    [
        "main.py:14: error: Unsupported sub-pattern in field binding: TpyOrPattern",
        "main.py:5: error: captures str parameter 'name' which would dangle (string_view into caller's storage)",
        "main.py:3: error: cannot assign std::optional<int32_t> here",
        "main.py:9: warning: value is a ::tpy::BigInt temporary",
        "main.py:19: error: a polymorphic `Own[Pet]` lowers to `unique_ptr<Pet>`",
    ],
)
def test_forbidden_pattern_fires_on_leaked_names(text: str) -> None:
    assert FORBIDDEN.search(text)


@pytest.mark.parametrize(
    "text",
    [
        "main.py:14: error: Unsupported sub-pattern in field binding: or-pattern",
        "main.py:2: error: 'int' has no attribute 'append'",
        "main.py:7: warning: copies Data into container; use copy() to make this explicit",
        "main.py:4: error: expected int32, got str",
    ],
)
def test_forbidden_pattern_stays_quiet_on_python_wording(text: str) -> None:
    assert not FORBIDDEN.search(text)
