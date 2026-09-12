# Error: 'n' locale-aware type in f-string format spec is not supported.
from tpy import int32


def main() -> None:
    x: int32 = int32(42)
    print(f"{x:n}")  # tpyc: error(/locale-aware.*is not supported/)

main()
