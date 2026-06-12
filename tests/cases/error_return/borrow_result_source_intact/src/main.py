# A BORROW returned from an @error_return callable ALIASES the source when
# bound to a local (pointer-repr `T | None` or plain): mutations through the
# local are visible on the source (CPython parity) and the source is never
# moved from. A FIELD target still receives a warned copy (copy() is the
# escape hatch), so the field leg only reads -- mutating h.dest would
# diverge from CPython's aliasing there (tracked acknowledged divergence).
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
        q = h.view()
    except E:
        print("error")
    if q is not None:
        q.items.append(4)
    print(len(h.src.items))

    v = Source()
    try:
        v = h.view()
    except E:
        print("error")
    v.items.append(5)
    print(len(h.src.items))

    h.grab()
    print(len(h.dest.items))
    print(len(h.src.items))


main()
