# Error: '_' grouping in f-string format spec is not supported.
from tpy import Int32


def main() -> None:
    x: Int32 = Int32(1234567)
    print(f"{x:_}")  # tpyc: error(/grouping is not supported/)

main()
