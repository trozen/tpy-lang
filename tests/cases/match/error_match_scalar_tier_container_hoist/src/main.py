# A SCALAR-subject match whose arms both bind a container read afterwards:
# the pointer-slot hoist is threaded through the record tier only.
# TPy rejects this `match n:` statement today.
from tpy import int32


def f(n: int32) -> int32:
    match n:  # tpyc: error(/stmt\.match/)
        case 0:
            # `xs` is bound per arm and read after the match.
            xs = [1, 2]
        case _:
            xs = [3]
    return xs[0] + len(xs)


def main() -> None:
    print(f(0))


main()
