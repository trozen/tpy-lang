# A PLAIN (non-error_return) borrow-returning call bound to a local inside a
# return-tier try (ReturnException handler) aliases the source, same as the
# @error_return shapes -- mutation through the local is visible on the source.
from tpy import int32, error_return, ReturnException


class E(Exception, ReturnException):
    pass


class H:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2]

    def view(self) -> list[int32]:
        return self.items

    @error_return(E)
    def poke(self) -> int32:
        return 1


def main() -> None:
    h = H()
    try:
        v = h.view()
        n = h.poke()
        v.append(9)
        print(h.items)
        print(n)
    except E:
        print("error")


main()
