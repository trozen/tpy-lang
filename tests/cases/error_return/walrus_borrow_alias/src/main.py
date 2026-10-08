# A walrus-bound borrow aliases its source like a statement binding: in a
# return-tier try (plain and @error_return callees), in while/if conditions
# (per-iteration rebind; used after); @readonly binds const; rvalue stays owned.
from tpy import int32, error_return, ReturnException, readonly, Own


class E(Exception, ReturnException):
    pass


class H:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2]

    def view(self) -> list[int32]:
        return self.items

    @readonly
    def rview(self) -> readonly[list[int32]]:
        return self.items

    @error_return(E)
    def poke(self) -> int32:
        return 1

    @error_return(E)
    def er_view(self) -> list[int32]:
        return self.items


def in_try(h: H) -> None:
    try:
        n = h.poke()
        if len(v := h.view()) > 0:
            v.append(9)
        print(h.items)
        print(n)
    except E:
        print("error")


def er_value(h: H) -> None:
    try:
        if len(u := h.er_view()) > 0:
            u.append(7)
        print(h.items)
    except E:
        print("error")


def in_while(h: H) -> None:
    while len(x := h.view()) < 6:
        x.append(50)
    print(h.items)


def in_if(h: H) -> None:
    if len(w := h.view()) > 0:
        w.append(8)
    print(len(w))
    print(h.items)


def readonly_walrus(h: H) -> None:
    if len(r := h.rview()) > 0:
        print(r[0])


def make() -> Own[list[int32]]:
    xs = [10, 20]
    return xs


def owned_walrus() -> None:
    if len(fresh := make()) > 0:
        fresh.append(30)
    print(fresh)


def main() -> None:
    h = H()
    in_try(h)
    er_value(h)
    in_while(h)
    in_if(h)
    readonly_walrus(h)
    owned_walrus()


main()
