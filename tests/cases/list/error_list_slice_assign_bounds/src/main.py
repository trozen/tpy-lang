# Error: slice bounds must be integers, not floats.
from tpy import int32

def main() -> None:
    a: list[int32] = [1, 2, 3, 4, 5]
    a[0.5:2] = [99]  # tpyc: error(/Slice bound must be an integer/)

main()
