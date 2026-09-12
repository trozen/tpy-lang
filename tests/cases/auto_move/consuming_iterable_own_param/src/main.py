# Iterating over an Iterable[Own[T]] parameter should move elements
# at last use within the loop body (per-element move from owned iterator).
from tpy import int32, Own
from typing import Iterable

class Item:
    value: int32
    def __init__(self, v: int32) -> None:
        self.value = v

def collect(source: Iterable[Own[Item]]) -> Own[list[Item]]:
    result: list[Item] = []
    for x in source:
        x.value += 10
        result.append(x)
    return result

def main() -> None:
    items: list[Item] = [Item(1), Item(2), Item(3)]
    out = collect(items)
    for r in out:
        print(r.value)

main()
