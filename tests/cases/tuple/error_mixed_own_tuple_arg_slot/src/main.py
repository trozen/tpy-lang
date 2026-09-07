# A MIXED `tuple[Own[A], B]` slot binds a const reference to the mixed render,
# never a move slot, so the Own-tuple decay must not fire.
from tpy import Int32, Own


class Alpha:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class Beta:
    m: Int32

    def __init__(self, m: Int32) -> None:
        self.m = m


def mixed_sink(p: tuple[Own[Alpha], Beta]) -> Int32:
    return p[0].n


def mixed_caller(p: tuple[Own[Alpha], Own[Beta]]) -> Int32:
    got = mixed_sink(p)  # tpyc: error(/call.arg_shape.tuple/)
    return got + p[0].n


def main() -> None:
    print(mixed_caller((Alpha(1), Beta(2))))


main()
