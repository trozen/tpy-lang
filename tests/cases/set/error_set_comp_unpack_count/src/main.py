# Error: tuple unpack count mismatch in set comprehension
from tpy import int32

def main() -> None:
    pairs: list[tuple[str, int32]] = [("a", 1)]
    s = {c for a, b, c in pairs}  # tpyc: error(/Cannot unpack tuple of 2.*into 3/)

main()
