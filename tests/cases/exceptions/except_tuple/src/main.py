# The `except (A, B):` tuple form. The parser expands it into one ordinary
# single-type clause per element, so each arm binds `e` at its own exact type --
# `.code` below resolves per-arm even though AErr and BErr declare it
# independently, with no common base but Exception.
class AErr(Exception):
    def __init__(self, code: int) -> None:
        super().__init__()
        self.code = code


class BErr(Exception):
    def __init__(self, code: int) -> None:
        super().__init__()
        self.code = code


class CErr(Exception):
    pass


def boom(which: int) -> None:
    if which == 0:
        raise AErr(10)
    if which == 1:
        raise BErr(20)
    raise CErr()


def with_binding(which: int) -> None:
    try:
        boom(which)
    except (AErr, BErr) as e:  # tpyc: ok
        print("tuple arm, code =", e.code)
    except CErr:
        # A sibling clause after the tuple form still matches in source order.
        print("sibling C arm")


def without_binding(which: int) -> None:
    try:
        boom(which)
    except (AErr, BErr):  # tpyc: ok
        print("no binding, caught", which)
    except CErr:
        print("no binding, C")


def single_element(which: int) -> None:
    # A one-element tuple is just the plain form.
    try:
        boom(which)
    except (AErr,):  # tpyc: ok
        print("single-element tuple")
    except Exception:
        print("fell through")


def repeated() -> None:
    # A type repeated in one tuple collapses to a single catch arm.
    try:
        raise AErr(1)
    except (AErr, AErr):  # tpyc: ok
        print("repeated element")


def main() -> None:
    for i in range(3):
        with_binding(i)
    for i in range(3):
        without_binding(i)
    single_element(0)
    single_element(1)
    repeated()


main()
