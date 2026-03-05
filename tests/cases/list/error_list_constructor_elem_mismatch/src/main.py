# list[T](iterable) rejects iterables with incompatible element types.
from tpy import Int32

def main() -> None:
    nums: list[Int32] = [1, 2, 3]
    bad = list[str](nums)  # tpyc: error(/cannot be constructed/)

main()
