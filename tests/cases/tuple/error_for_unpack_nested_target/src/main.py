# Error: nested target in for-loop unpacking
from tpy import int32

def main() -> None:
    items: list[tuple[tuple[int32, int32], str]] = []
    for (a, b), c in items:  # tpyc: error(/targets must be simple variables/)
        pass

main()
