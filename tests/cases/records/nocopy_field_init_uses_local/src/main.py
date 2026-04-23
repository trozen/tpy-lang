# Regression: a nocopy-typed field whose __init__ initializer references a
# body-local must stay in the constructor body. It used to hoist into the C++
# member-initializer list (`Foo() : _x(Wrap(tmp))`) where `tmp` is undeclared.
from tpy import Int32, nocopy


@nocopy
class Wrap:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v


def pick(n: Int32) -> Int32:
    return n + Int32(1)


class Foo:
    _x: Wrap

    def __init__(self, seed: Int32) -> None:
        tmp = pick(seed)
        self._x = Wrap(tmp)


def main() -> None:
    f = Foo(Int32(10))
    print(f._x.v)


main()
