from tpy import int32
import b
import w


class Adder:
    def __init__(self, k: int32) -> None:
        self.k = k

    # `W[Val]` stores its `Val` by value, so the in-class `operator+` taking it
    # by value needs `Val` complete where `a.hpp` sees it forward-declared.
    def __add__(self, x: w.W[b.Val]) -> int32:  # tpyc: error(/Cyclic import: parameter 'x'/)
        return self.k + x.p.n


def run() -> int32:
    v = b.Val(3)
    return Adder(2) + w.W[b.Val](v)
