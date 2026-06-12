# An @error_return result assigned to a pointer-repr `T | None` local moves into
# the local's rebind slot (not an ill-formed `T* = T`). @nocopy forces the move.
from tpy import Int32, Own, nocopy, error_return, ReturnException


class Bad(Exception, ReturnException):
    pass


@nocopy
class Box:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


@error_return(Bad)
def decode(ok: bool) -> Own[Box]:
    if ok:
        return Box(7)
    raise Bad


def run(ok: bool) -> Int32:
    b: Box | None = None
    try:
        b = decode(ok)          # result -> pointer-repr Optional local (rebind slot)
    except Bad:
        return -1
    if b is not None:
        return b.v
    return -2


def main() -> None:
    print(run(True))
    print(run(False))


main()
