# Error: '=' alignment in f-string format spec is not supported.
from tpy import Int32


def main() -> None:
    x: Int32 = Int32(-42)
    print(f"{x:=10}")  # tpyc: error(/alignment is not supported/)

main()
