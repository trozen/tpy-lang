# The handler sibling of with_target_finally_read: the `with` target is read ONLY
# from an `except` body, and the try body raises. Same reachability -- the handler
# runs on the exception path, so the read keeps the target live entering the
# statement and the manager has to outlive its branch.
from tpy import int32


class Reg:
    n: int32

    def __init__(self, n: int32):
        self.n = n

    def __enter__(self) -> "Reg":
        return self

    def __exit__(self, et, ev, tb) -> None:
        pass

    def __del__(self) -> None:
        self.n = -999


def probe(flag: bool) -> int32:
    if flag:
        with Reg(11) as view:
            pass
    else:
        with Reg(22) as view:
            pass

    total = 0
    try:
        raise ValueError("stop")
    except ValueError:
        total += view.n  # the only read of `view`, in the handler
    return total


def main() -> None:
    print(probe(True))
    print(probe(False))


main()
