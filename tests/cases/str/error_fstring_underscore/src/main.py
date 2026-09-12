# Error: '_' grouping in f-string format spec is not supported.
from tpy import int32


def main() -> None:
    x: int32 = int32(1234567)
    print(f"{x:_}")  # tpyc: error(/grouping is not supported/)

main()
