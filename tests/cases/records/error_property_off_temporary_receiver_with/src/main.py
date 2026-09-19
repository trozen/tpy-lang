# The WITH-MANAGER position of a borrow-returning @property read off a
# TEMPORARY receiver: the manager slot holds its value for the whole block,
# long after the `mk()` temporary dies at the end of the header statement.
# The tag names the SINK; the sink table is in docs/PROPERTY_DESIGN.md.
from tpy import Own, int32


class Guard:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __enter__(self) -> int32:
        self.n += 1
        return self.n

    def __exit__(self, kind, value, tb) -> None:
        pass


GUARD: Guard = Guard()


class H:
    tag: int32

    def __init__(self) -> None:
        self.tag = 0

    @property
    def guard(self) -> Guard:
        return GUARD


def mk() -> Own[H]:
    return H()


def main() -> None:
    with mk().guard as g:  # tpyc: error(/with_manager.lends_from_temporary/)
        print(g)


main()
