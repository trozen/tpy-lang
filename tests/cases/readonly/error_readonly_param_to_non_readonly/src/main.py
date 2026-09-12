# @readonly: passing a param to a non-readonly function is rejected.
from tpy import int32, readonly


class Box:
    value: int32

    def __init__(self, v: int32):
        self.value = v


def mutate(b: Box) -> None:
    b.value = b.value + 1


@readonly
def bad(b: Box) -> None:
    mutate(b)  # tpyc: error(/readonly/)
