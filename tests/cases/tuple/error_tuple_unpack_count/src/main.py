# Error: wrong number of targets in tuple unpacking
from tpy import Int32

def get_pair() -> tuple[Int32, Int32]:
    return (Int32(1), Int32(2))

def main() -> None:
    a, b, c = get_pair()  # tpyc: error(/Cannot unpack tuple of 2 elements into 3 targets/)

main()
