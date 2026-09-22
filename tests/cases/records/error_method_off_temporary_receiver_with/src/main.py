# The METHOD twin of error_property_off_temporary_receiver_with: a
# borrow-returning method call binds the `with` manager borrowed, so the
# receiver must outlive the block -- a TEMPORARY one does not, and the
# manager gate refuses it rather than binding into a destroyed object.
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

    def guard(self) -> Guard:
        return GUARD


def mk() -> Own[H]:
    return H()


def main() -> None:
    with mk().guard() as g:  # tpyc: error(/stmt.with/)
        print(g)


main()
