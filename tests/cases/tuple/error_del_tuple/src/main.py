# del on tuple elements is not supported (tuples are immutable)
from tpy import int32

def main() -> None:
    t = (int32(1), int32(2), int32(3))
    del t[0]  # tpyc: error(/immutable/)

main()
