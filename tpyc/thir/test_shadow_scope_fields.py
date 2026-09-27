"""Every field of the lowering-state owners `shadow_scope` scrubs is
classified exactly once in `tpyc/thir/lower/context.py`: shadowed, not
name-keyed, or inherited by design. The scrub lists its fields by hand, so a
new field is caught here instead of silently inheriting into inner scopes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import fields

import pytest

from .lower import context as ctx

_WHY = ("classify it in tpyc/thir/lower/context.py: a name-keyed field "
        "missing from the shadowed set leaks the enclosing function's facts "
        "into a nested def or lambda that rebinds the name")

_OWNERS = {
    "_LowerCtx": (
        frozenset(ctx._LowerCtx.__slots__),
        {"shadowed": ctx._SHADOWED_LC_STATE + ctx._SHADOWED_LC_BY_HAND,
         "not name-keyed": ctx._LC_NOT_NAME_KEYED,
         "inherited by design": ctx._LC_INHERITED_BY_DESIGN}),
    "_Prescan": (
        frozenset(ctx._Prescan.__slots__),
        {"shadowed": ctx._SHADOWED_PRESCAN_FACTS,
         "not name-keyed": ctx._PRESCAN_NOT_NAME_KEYED,
         "inherited by design": ctx._PRESCAN_INHERITED_BY_DESIGN}),
    "_NarrowScope": (
        frozenset(f.name for f in fields(ctx._NarrowScope)),
        {"shadowed": ctx._SHADOWED_NARROW_FACTS,
         "not name-keyed": ctx._NARROW_NOT_NAME_KEYED,
         "inherited by design": ctx._NARROW_INHERITED_BY_DESIGN}),
}


@pytest.mark.parametrize("owner", sorted(_OWNERS))
def test_every_field_classified_once(owner: str) -> None:
    actual, classes = _OWNERS[owner]
    counts = Counter(f for names in classes.values() for f in names)
    problems = []
    for f in sorted(actual - counts.keys()):
        problems.append(f"{owner}.{f} is unclassified")
    for f, n in sorted(counts.items()):
        if n > 1:
            where = [c for c, names in classes.items() for g in names if g == f]
            problems.append(f"{owner}.{f} is classified {n} times "
                            f"({', '.join(where)})")
    for f in sorted(counts.keys() - actual):
        problems.append(f"{owner}.{f} is classified but is not a field")
    assert not problems, "\n".join(problems) + "\n" + _WHY
