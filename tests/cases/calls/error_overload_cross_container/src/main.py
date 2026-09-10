# Overload resolution must not cross-match unrelated containers.
# Phase D collapsed list/set/dict/Array/Span to a shared NominalType class;
# a latent `type(arg) == type(param)` check in type_matches_numeric let
# element-only matching fire for list vs set pairs. This pins that a
# list[Int32] argument does not silently match a set[Int32] overload.
from typing import overload
from tpy import Int32

@overload
def take(x: set[Int32]) -> None: ...

@overload
def take(x: dict[Int32, Int32]) -> None: ...

def take(x: set[Int32] | dict[Int32, Int32]) -> None:
    pass

def main() -> None:
    items: list[Int32] = [Int32(1), Int32(2)]
    take(items)  # tpyc: error(/No matching overload|Type mismatch/)

main()
