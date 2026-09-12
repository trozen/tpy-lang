# Mixed key types in dict literal should error
from tpy import int32

def main() -> None:
    d = {"a": int32(1), int32(2): int32(3)}  # tpyc: error(/mixed key types/)

main()
