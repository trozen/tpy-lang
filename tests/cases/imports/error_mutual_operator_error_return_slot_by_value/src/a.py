from tpy import ReturnException, error_return, int32
import b


class Refused(Exception, ReturnException):
    pass


class Pairer:
    def __init__(self, k: int32) -> None:
        self.k = k

    @error_return(Refused)
    def __mul__(self, x: b.Item) -> tuple[b.Item, int32]:  # tpyc: error(/Cyclic import: return type/)
        if x.n < 0:
            raise Refused()
        return (x, self.k)


def run() -> int32:
    it = b.Item(3)
    try:
        r, k = Pairer(2) * it
        return r.n + k
    except Refused:
        return 0
