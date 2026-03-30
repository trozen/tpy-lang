# Error: map with too many iterables (max 5 supported)
from tpy import Int32

def f6(a: Int32, b: Int32, c: Int32, d: Int32, e: Int32, f: Int32) -> Int32:
    return a

def main() -> None:
    map(f6, [1], [2], [3], [4], [5], [6])  # tpyc: error(/No matching overload|not a variable/)

main()
