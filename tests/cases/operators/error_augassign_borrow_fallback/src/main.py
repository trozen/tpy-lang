# `a += b` with no __iadd__ falls back to __add__; a BORROW-returning
# fallback is rejected -- the in-place update would copy where CPython
# rebinds the name to the returned object (rebind-as-alias for aug-assign
# targets is unimplemented, see BUGS.md). Own-returning fallbacks stay
# accepted (op_own_return_fresh).
from tpy import Int32


class Acc:
    n: Int32

    def __init__(self, n: Int32):
        self.n = n

    def __add__(self, o: "Acc") -> "Acc":
        return o


def main():
    a = Acc(1)
    b = Acc(2)
    a += b  # tpyc: error(/falls back to '__add__', which returns a borrow/)
    print(a.n)


main()
