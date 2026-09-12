# Overload resolution must not cross-match unrelated containers.
# Phase D collapsed list/set/dict/Array/Span to a shared NominalType class;
# a latent `type(arg) == type(param)` check in type_matches_numeric let
# element-only matching fire for list vs set pairs. This pins that a
# list[int32] argument does not silently match a set[int32] overload.
from typing import overload
from tpy import int32

@overload
def take(x: set[int32]) -> None: ...

@overload
def take(x: dict[int32, int32]) -> None: ...

def take(x: set[int32] | dict[int32, int32]) -> None:
    pass

def main() -> None:
    items: list[int32] = [int32(1), int32(2)]
    take(items)  # tpyc: error(/No matching overload|Type mismatch/)

main()
