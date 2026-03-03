# del on tuple elements is not supported (tuples are immutable)
from tpy import Int32

def main() -> None:
    t = (Int32(1), Int32(2), Int32(3))
    del t[0]  # tpyc: error(/immutable/)

main()
