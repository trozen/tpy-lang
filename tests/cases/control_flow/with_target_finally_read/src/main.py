# A `with` target read ONLY from a `finally`, where the try body raises. The
# manager must outlive its branch: an exception can leave the try at any point,
# so the finally's read is reachable and keeps the target live entering the
# statement. Treating the raise as the end of all flow drops that read.
from tpy import Int32


class Reg:
    n: Int32

    def __init__(self, n: Int32):
        self.n = n

    def __enter__(self) -> "Reg":
        return self

    def __exit__(self, et, ev, tb) -> None:
        pass

    def __del__(self) -> None:
        self.n = -999  # an early drop is visible in the printed total


def probe(flag: bool) -> Int32:
    if flag:
        with Reg(11) as view:
            pass
    else:
        with Reg(22) as view:
            pass

    total = 0
    try:
        raise ValueError("stop")
    finally:
        total += view.n  # the only read of `view`, on the exception path
        print("finally saw", total)
    return total


def main() -> None:
    try:
        probe(True)
    except ValueError:
        print("caught 1")
    try:
        probe(False)
    except ValueError:
        print("caught 2")


main()
