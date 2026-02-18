# Inference through Own[T] still catches type mismatches.
from tpy import Int32, Own


class Box:
    value: Int32


def merge[T](a: Own[T], b: Own[T]) -> None:
    pass


def main():
    b = Box()
    b.value = 1
    nums: list[Int32] = [10, 20]
    # T can't be both Box and list[Int32]
    merge(b, nums)  # tpyc: error(/Cannot infer type arguments/)
