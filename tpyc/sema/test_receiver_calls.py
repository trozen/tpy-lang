"""The hint a write through a readonly method result carries
(`implicit_readonly_result_hint`): it names the cause that made the result
readonly, `@pure` before a frozen class before the dunder rule."""

from __future__ import annotations

import pytest

from ..diagnostics import SemanticError
from ..thir.testutil import _compile

SOURCE = """\
from dataclasses import dataclass
from tpy import int32, pure

class Item:
    n: int32
    def __init__(self) -> None:
        self.n = 0
    def __eq__(self, o: "Item") -> bool:
        return self.n == o.n
    def __hash__(self) -> int:
        return hash(self.n)

@dataclass(frozen=True)
class Frozen:
    item: Item

    {decorator}
    def get(self) -> Item:
        return self.item

def main() -> None:
    f = Frozen(Item())
    f.get().n = 5
"""


@pytest.mark.parametrize("decorator, hint", [
    # `@pure` in a frozen class: the decorator is named first.
    ("@pure", r"'@pure' method 'get' returns it readonly; drop @pure, or "
              r"copy\(\) the result"),
    ("", r"method 'get' of a frozen record returns it readonly; mark it "
         r"@readonly\(False\), or copy\(\) the result"),
], ids=["frozen_pure", "frozen"])
def test_readonly_result_hint_names_the_cause(decorator: str, hint: str) -> None:
    with pytest.raises(SemanticError, match=hint):
        _compile(SOURCE.format(decorator=decorator))
