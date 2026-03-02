# Tuple as function parameter (passed by const ref)
from tpy import Int32

def print_pair(p: tuple[Int32, str]) -> None:
    print(p[0])
    print(p[1])

def sum_triple(t: tuple[Int32, Int32, Int32]) -> Int32:
    return t[0] + t[1] + t[2]

def main() -> None:
    pair = (Int32(5), "five")
    print_pair(pair)

    nums = (Int32(10), Int32(20), Int32(30))
    result = sum_triple(nums)
    print(result)

main()
