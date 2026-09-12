# Error: map with too many iterables (max 5 supported)
from tpy import int32

def f6(a: int32, b: int32, c: int32, d: int32, e: int32, f: int32) -> int32:
    return a

def main() -> None:
    map(f6, [1], [2], [3], [4], [5], [6])  # tpyc: error(/No matching overload|not a variable/)

main()
