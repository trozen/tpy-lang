# A parameter of a reference type cannot be rebound, through `nonlocal` from a
# nested def as in the function body (see docs/LANGUAGE_FEATURES.md).
from tpy import int32


class V:
    def __init__(self, n: int32) -> None:
        self.n = n


def f(v: V) -> None:
    def g() -> None:
        nonlocal v
        # the subject: a plain rebind of the enclosing parameter
        v = V(5)  # tpyc: error(/Cannot reassign parameter 'v' of type 'V'/)
    g()
    print(v.n)


def main() -> None:
    f(V(1))


main()
