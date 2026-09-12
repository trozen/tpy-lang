# Inheriting the ctor of a generic base whose type arg is a cross-module
# (namespace-qualified in C++) type must emit a valid using-declaration.
from tpy import int32
from other import Key


class Holder[K, V]:
    _k: K
    _v: V

    def __init__(self, k: K, v: V):
        self._k = k
        self._v = v


class Sub(Holder[Key, int32]):
    pass


def main() -> None:
    s = Sub(Key(5), 7)
    print(s._k.x)
    print(s._v)


main()
