# Test error: dict() from non-tuple iterable
from tpy import int32

def main() -> None:
    nums: list[int32] = [1, 2, 3]
    d = dict(nums)  # tpyc: error(/cannot be constructed/)

main()
