# Error: tuple unpack count mismatch in dict comprehension
from tpy import Int32

def main() -> None:
    pairs: list[tuple[str, Int32]] = [("a", 1)]
    d = {a: c for a, b, c in pairs}  # tpyc: error(/Cannot unpack tuple of 2.*into 3/)

main()
