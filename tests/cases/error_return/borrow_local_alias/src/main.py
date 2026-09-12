# A borrow from an @error_return callable bound to a local inside try aliases
# the source (mutations visible, CPython parity); same through the
# auto-propagate path; a @readonly borrow binds const and reads work.
from tpy import int32, error_return, ReturnException, readonly


class E(Exception, ReturnException):
    pass


class H:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2]

    @error_return(E)
    def view(self) -> list[int32]:
        return self.items

    @error_return(E)
    @readonly
    def rview(self) -> list[int32]:
        return self.items


@error_return(E)
def propagate(h: H) -> int32:
    v = h.view()
    v.append(8)
    return len(h.items)


def main() -> None:
    h = H()
    try:
        v = h.view()
        v.append(9)
        print(h.items)
    except E:
        print("error")

    try:
        print(propagate(h))
    except E:
        print("error")

    try:
        r = h.rview()
        print(len(r))
    except E:
        print("error")


main()
