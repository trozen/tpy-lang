# Regression: a nocopy-typed field whose __init__ initializer references a
# body-local must stay in the constructor body. It used to hoist into the C++
# member-initializer list (`Foo() : _x(Wrap(tmp))`) where `tmp` is undeclared.
from tpy import int32, nocopy


@nocopy
class Wrap:
    v: int32
    def __init__(self, v: int32) -> None:
        self.v = v


def pick(n: int32) -> int32:
    return n + int32(1)


class Foo:
    _x: Wrap

    def __init__(self, seed: int32) -> None:
        tmp = pick(seed)
        self._x = Wrap(tmp)


def main() -> None:
    f = Foo(int32(10))
    print(f._x.v)


main()
