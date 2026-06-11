# The hidden-temp desugar must preserve Python's "evaluate the whole RHS
# before binding any target" rule: swaps and self-referential RHS stay
# correct, and a target is left unbound when a later element raises.
from tpy import Int32


def boom() -> Int32:
    raise ValueError("nope")


def swap() -> None:
    a = 1
    b = 2
    a, b = (b, a)
    print(a)
    print(b)


def fib() -> None:
    x = 0
    y = 1
    for _ in range(5):
        x, y = (y, x + y)
    print(x)
    print(y)


def eval_order() -> None:
    a = 7
    b = 8
    try:
        a, b = (70, boom())
    except ValueError:
        pass
    # The RHS raised before any visible target was bound -> both unchanged.
    print(a)
    print(b)


def main() -> None:
    swap()
    fib()
    eval_order()


main()
