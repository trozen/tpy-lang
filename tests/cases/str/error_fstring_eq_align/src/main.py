# Error: '=' alignment in f-string format spec is not supported.
from tpy import int32


def main() -> None:
    x: int32 = int32(-42)
    print(f"{x:=10}")  # tpyc: error(/alignment is not supported/)

main()
