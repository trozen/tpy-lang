# A nested def rebinding the enclosing record local through `nonlocal` to an
# existing object is refused by walrus as by `=`
# (BUGS.md#nonlocal-reference-rebind-copies).
from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def f(q: Box) -> None:
    a = Box(1)

    def g() -> None:
        nonlocal a
        # The rebind to the parameter's object is the subject.
        print((a := q).n)  # tpyc: error(/rebinding nonlocal 'a' is not supported yet: the write would replace the existing object rather than rebind the name/)

    g()
    a.n += 100
    print(q.n, a.n)


f(Box(5))
