# A second module with its own `Outer.Inner` and `Err`, never to be confused
# with m's.
from tpy import Own


class Err(Exception):
    pass


# Shadows the builtin of the same name inside this module.
class TimeoutError(Exception):
    pass


class Outer:
    class Inner:
        v: int

        def __init__(self, v: int) -> None:
            self.v = v * 100

    class Err(Exception):
        pass


def make_inner(v: int) -> Own[Outer.Inner]:
    i = Outer.Inner(v)
    return i
