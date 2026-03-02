# Error: nested target in for-loop unpacking
from tpy import Int32

def main() -> None:
    items: list[tuple[tuple[Int32, Int32], str]] = []
    for (a, b), c in items:  # tpyc: error(/targets must be simple variables/)
        pass

main()
