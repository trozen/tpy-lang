# Error: wrong number of targets in tuple unpacking
from tpy import int32

def get_pair() -> tuple[int32, int32]:
    return (int32(1), int32(2))

def main() -> None:
    a, b, c = get_pair()  # tpyc: error(/Cannot unpack tuple of 2 elements into 3 targets/)

main()
