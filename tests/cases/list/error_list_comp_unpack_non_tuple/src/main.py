# Error: tuple unpack on non-tuple iterable in comprehension
from tpy import int32

def main() -> None:
    items: list[int32] = [1, 2, 3]
    bad = [a for a, b in items]  # tpyc: error(/Cannot unpack non-tuple/)

main()
