# An `Own[list | None]` constructor argument: the narrowed-field element write
# routes, but the ctor argument slot itself has no row. Concretely,
# `Args([5, 6])` passes a fresh list where the ctor param is
# `Own[list[int32] | None]`; TPy rejects that call today.
from tpy import int32, Own


class Args:
    coord: list[int32] | None

    def __init__(self, c: Own[list[int32] | None]) -> None:
        self.coord = c


def write(a: Args) -> None:
    assert a.coord is not None
    a.coord[0] = 9


def main() -> None:
    a = Args([5, 6])  # tpyc: error(/call.ctor_arg.own_optional/)
    write(a)
    print(a.coord)


main()
