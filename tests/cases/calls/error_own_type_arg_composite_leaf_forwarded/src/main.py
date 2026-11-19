# Error: a generic handing `T` on to one holding it inside `T | None` is refused
# at the OUTER `Own[P]` call: BUGS.md#own-type-arg-composite-leaf-refused.
from tpy import Own, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def opt_len[T](x: T | None) -> int32:
    return 0 if x is None else 1


def outer[T](x: T) -> int32:
    return opt_len[T](x)


def main() -> None:
    # The subject: `outer`'s own signature names no composite.
    print(outer[Own[P]](P(1)))  # tpyc: error(/type argument of 'outer' is not supported yet: 'T' of 'opt_len' appears inside 'T \| None'/)


main()
