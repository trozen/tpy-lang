# MIR pins for a shadowed inherited field: `Shadow::n` and `Base::n` are two
# storages (a warned CPython divergence -- CPython has one attribute), and the
# explicit `Base.n` spelling (TPy-only) names the ancestor's.
from tpy import int32, readonly


class Base:
    n: int32

    def __init__(self) -> None:  # tpyc: mir(covered)
        self.n = 0

    # base method: writes Base::n, also on a Shadow receiver
    def bump(self) -> None:
        self.n += 1

    @readonly
    def base_n(self) -> int32:
        return self.n


class Shadow(Base):
    n: int32  # tpyc: warning(/shadows inherited field/)

    def __init__(self) -> None:  # tpyc: mir(covered)
        super().__init__()
        self.n = 100

    # subclass method: writes Shadow::n
    def own_bump(self) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.n += 1

    # explicit ancestor field: `Base.n` is Base::n, found by the walk from the named ancestor
    @readonly
    def both(self) -> int32:  # tpyc: mir(covered)
        return self.n + Base.n


class Deeper(Shadow):
    def __init__(self) -> None:  # tpyc: mir(covered)
        super().__init__()

    # explicit ancestor field through an intermediate class: `Shadow.n` is Shadow::n
    @readonly
    def mid(self) -> int32:  # tpyc: mir(covered)
        return Shadow.n + Base.n


# free function: the base's method and the subclass's write two storages
def use_shadow(sh: Shadow) -> int32:  # tpyc: mir(covered)
    sh.bump()
    sh.own_bump()
    return sh.both() * 1000 + sh.base_n() * 10 + sh.n


def main() -> None:
    sh = Shadow()
    print("shadow", use_shadow(sh))
    d = Deeper()
    d.bump()
    print("deeper", d.mid())


main()
