# Records, an exception and helpers the hoist sections share.
from tpy import int32, Ptr, ReturnException, nocopy


@nocopy
class Flat:
    def __init__(self, n: int32) -> None:
        self.n = n


class Pic:
    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 1


class CM:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __enter__(self) -> int32:
        return self.n

    def __exit__(self, et, ev, tb) -> None:
        pass


class MyErr(Exception, ReturnException):
    pass


def halves(n: int32) -> tuple[int32, int32]:
    return (n // 2, n - n // 2)


def take_ptr(p: Pic) -> Ptr[Pic]:
    return p
