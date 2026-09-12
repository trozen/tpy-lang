# @readonly: subscript assignment on a param is rejected.
from tpy import int32, readonly


@readonly
def bad(items: list[int32], i: int32) -> None:
    items[i] = 0  # tpyc: error(/Cannot mutate readonly reference/)
