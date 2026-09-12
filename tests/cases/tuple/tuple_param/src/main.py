# Tuple as function parameter (passed by const ref)
from tpy import int32

def print_pair(p: tuple[int32, str]) -> None:
    print(p[0])
    print(p[1])

def sum_triple(t: tuple[int32, int32, int32]) -> int32:
    return t[0] + t[1] + t[2]

def main() -> None:
    pair = (int32(5), "five")
    print_pair(pair)

    nums = (int32(10), int32(20), int32(30))
    result = sum_triple(nums)
    print(result)

main()
