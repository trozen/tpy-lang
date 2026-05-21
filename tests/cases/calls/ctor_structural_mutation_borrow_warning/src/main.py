# Inverse of the precision tightening in warning_implicit_copy: when a
# constructor's body DOES structurally mutate one of its params (here
# `xs.append(...)`), passing a borrowed container at the call site
# still triggers the "Passing borrowed container ..." warning. Pins
# that the precision win in `_check_borrow_arg_conflicts` (only suppress
# when callee provably doesn't structurally mutate) doesn't silently
# drop the real-bug case.
from tpy import Int32


class Box:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v


def first(xs: list[Box]) -> Box:
    return xs[0]


class Sink:
    def __init__(self, xs: list[Box]) -> None:
        xs.append(Box(Int32(99)))  # structural mutation -- may invalidate `first(...)`'s result


def main() -> None:
    items = [Box(Int32(1)), Box(Int32(2))]
    head = first(items)  # element borrow into items
    print(head.v)
    _ = Sink(items)  # tpyc: warning(/borrowed container/)


main()
