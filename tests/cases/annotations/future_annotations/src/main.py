# Regression guard: `from __future__ import annotations` is silently ignored
# by tpyc (it is a CPython runtime directive that does not affect ast.parse
# output, so tpyc sees the same Subscript/BinOp/Name annotation nodes as
# usual). This test confirms the import is accepted and does not perturb
# parsing of the surrounding annotations.
from __future__ import annotations
from tpy import int32


class Apple:
    def __init__(self) -> None:
        pass


class Banana:
    def __init__(self) -> None:
        pass


def first(items: list[int32]) -> int32:
    return items[0]


def take(f: Apple | Banana) -> None:
    if isinstance(f, Apple):
        print("apple")
    else:
        print("banana")


def main() -> None:
    print(first([int32(11), int32(12), int32(13)]))
    take(Apple())
    take(Banana())


main()
