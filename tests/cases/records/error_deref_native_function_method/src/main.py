# A payload method resolving to a function-form native (`::tpy::pop_back(recv)`)
# reached THROUGH a user `__deref__`: the deref chain cannot be threaded into the
# symbol's receiver slot.
from tpy import int32


class Wrap:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1]

    def __deref__(self) -> list[int32]:
        return self.xs


def last() -> int32:
    w = Wrap()
    return w.pop()  # tpyc: error(/method.marker.deref.builtin/)


def main() -> None:
    print(last())


main()
