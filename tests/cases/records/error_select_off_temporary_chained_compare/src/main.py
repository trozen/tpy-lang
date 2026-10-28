# A record select with one fresh arm is held in a slot and stays an lvalue,
# so a chained comparison binding it by reference would keep its other arm
# -- a reference a method hands back off a temporary -- past its storage;
# it is refused (a value select with a fresh arm is a prvalue and binds).
# WORKAROUND: bind the receiver first -- `h = mk()` then `h.getp()`.
from tpy import Own, int32


class Pt:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __lt__(self, o: "Pt") -> bool:
        return self.n < o.n


class H:
    p: Pt

    def __init__(self) -> None:
        self.p = Pt(5)

    def getp(self) -> Pt:
        return self.p


def mk() -> Own[H]:
    return H()


def main(flag: bool) -> None:
    print(Pt(1) < (mk().getp() if flag else Pt(2)) < Pt(9))  # tpyc: error(/lends_from_temporary/)


main(True)
