from tpy import ReturnException, error_return, int32
import b


class Refused(Exception, ReturnException):
    pass


class Picker:
    def __init__(self, k: int32) -> None:
        self.k = k

    @error_return(Refused)
    def __xor__(self, x: b.Item) -> b.Item | None:  # tpyc: error(/Cyclic import: return type/)
        if x.n < 0:
            raise Refused()
        if x.n > self.k:
            return x
        return None


def run() -> int32:
    it = b.Item(3)
    try:
        r = Picker(2) ^ it
        if r is not None:
            return r.n
        return 0
    except Refused:
        return -1
