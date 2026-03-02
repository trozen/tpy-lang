# Mixed key types in dict literal should error
from tpy import Int32

def main() -> None:
    d = {"a": Int32(1), Int32(2): Int32(3)}  # tpyc: error(/mixed key types/)

main()
