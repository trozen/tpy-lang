# Error: 'n' locale-aware type in f-string format spec is not supported.
from tpy import Int32


def main() -> None:
    x: Int32 = Int32(42)
    print(f"{x:n}")  # tpyc: error(/locale-aware.*is not supported/)

main()
