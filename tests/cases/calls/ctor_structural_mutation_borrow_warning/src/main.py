# Inverse of the precision tightening in warning_implicit_copy: when a
# constructor's body DOES structurally mutate one of its params (here
# `xs.append(...)`), passing a borrowed container at the call site
# still triggers the "Passing borrowed container ..." warning. Pins
# that the precision win in `_check_borrow_arg_conflicts` (only suppress
# when callee provably doesn't structurally mutate) doesn't silently
# drop the real-bug case.
from tpy import int32


class Box:
    v: int32
    def __init__(self, v: int32) -> None:
        self.v = v


def first(xs: list[Box]) -> Box:
    return xs[0]


class Sink:
    def __init__(self, xs: list[Box]) -> None:
        xs.append(Box(int32(99)))  # structural mutation -- may invalidate `first(...)`'s result


def main() -> None:
    items = [Box(int32(1)), Box(int32(2))]
    head = first(items)  # element borrow into items
    print(head.v)
    _ = Sink(items)  # tpyc: warning(/borrowed container/)


main()
