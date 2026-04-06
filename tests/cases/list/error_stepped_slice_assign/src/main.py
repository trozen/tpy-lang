# Error: stepped slice assignment is not supported.
from tpy import Int32

def main() -> None:
    items: list[Int32] = [Int32(1), Int32(2), Int32(3), Int32(4), Int32(5)]
    items[::Int32(2)] = [Int32(10), Int32(30), Int32(50)]  # tpyc: error(/Stepped slice assignment/)

main()
