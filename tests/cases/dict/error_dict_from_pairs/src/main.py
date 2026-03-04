# Test error: dict() from non-tuple iterable
from tpy import Int32

def main() -> None:
    nums: list[Int32] = [1, 2, 3]
    d = dict(nums)  # tpyc: error(/cannot be constructed/)

main()
