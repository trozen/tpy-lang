# @readonly: subscript assignment on a param is rejected.
from tpy import Int32, readonly


@readonly
def bad(items: list[Int32], i: Int32) -> None:
    items[i] = 0  # tpyc: error(/Cannot mutate readonly reference/)
