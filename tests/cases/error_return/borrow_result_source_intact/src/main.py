# A BORROW returned from an @error_return callable must never be moved from:
# the source object stays intact after assigning the result to a pointer-repr
# `T | None` local, a plain storage local, and a field. The targets receive a
# COPY for now (intended here; the copy-vs-alias divergence from CPython is
# tracked in BUGS.md as the @error_return borrow-copy entry), so this test
# only reads -- it guards source integrity, not aliasing.
from tpy import Int32, error_return, ReturnException


class E(Exception, ReturnException):
    pass


class Source:
    items: list[Int32]

    def __init__(self) -> None:
        self.items = [1, 2, 3]


class Holder:
    src: Source
    dest: Source

    def __init__(self) -> None:
        self.src = Source()
        self.dest = Source()

    @error_return(E)
    def view(self) -> Source:
        return self.src

    def grab(self) -> None:
        try:
            self.dest = self.view()  # tpyc: warning(/copies Source into field/)
        except E:
            print("error")


def main() -> None:
    h = Holder()

    q: Source | None = None
    try:
        q = h.view()  # pointer-repr local: rebind slot must not steal from h.src
    except E:
        print("error")
    if q is not None:
        print(len(q.items))
    print(len(h.src.items))

    v = Source()
    try:
        v = h.view()  # direct assign path: no rebind slot involved
    except E:
        print("error")
    print(len(v.items))
    print(len(h.src.items))

    h.grab()
    print(len(h.dest.items))
    print(len(h.src.items))


main()
