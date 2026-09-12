# Recursion witness (if bodies) + fail-fast: a raise nested in an `if` still
# triggers the whole-body wrap (else -Werror=terminate) and aborts at runtime.
# Warns at compile. CPython continues, so no_cpython.
from tpy import int32


class Maybe:
    _n: int32

    def __init__(self, n: int32):
        self._n = n

    def __del__(self):
        print("del", self._n)
        if self._n > 0:
            raise ValueError("positive on drop")  # tpyc: warning(/.raise. in .__del__. cannot propagate/)


def main():
    m = Maybe(4)
    print("in main")


main()
