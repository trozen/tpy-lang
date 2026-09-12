# list[T](iterable) rejects iterables with incompatible element types.
from tpy import int32

def main() -> None:
    nums: list[int32] = [1, 2, 3]
    bad = list[str](nums)  # tpyc: error(/cannot be constructed/)

main()
