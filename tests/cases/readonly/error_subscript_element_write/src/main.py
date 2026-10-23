# An element read off a readonly receiver is readonly (the `@auto_readonly`
# `__getitem__` answers with its const twin): a write through it is refused.
from tpy import int32, readonly


class P:
    def __init__(self, v: int32) -> None:
        self.v = v


def touch(xs: readonly[list[P]]) -> None:
    xs[0].v = 9  # tpyc: error(/readonly/)


def main() -> None:
    touch([P(1)])


main()
