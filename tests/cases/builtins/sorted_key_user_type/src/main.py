# sorted/min/max with key returning a user-defined Comparable type
from __future__ import annotations
from tpy import Int32, Own

class Score:
    val: Int32
    def __init__(self, val: Int32) -> None:
        self.val = val
    def __lt__(self, other: Score) -> bool:
        return self.val < other.val

class Item:
    name: str
    score: Score
    def __init__(self, name: str, score: Own[Score]) -> None:
        self.name = name
        self.score = score

def main() -> None:
    items = [Item("c", Score(3)), Item("a", Score(1)), Item("b", Score(2))]

    # sorted by user-defined Comparable key
    result = sorted(items, key=lambda it: it.score)
    for r in result:
        print(r.name)

    # stability: equal scores preserve original order
    items2 = [Item("x", Score(1)), Item("y", Score(1)), Item("z", Score(1))]
    result2 = sorted(items2, key=lambda it: it.score)
    for r in result2:
        print(r.name)

main()
