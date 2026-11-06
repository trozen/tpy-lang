# Builtin bases reached through a user class (DBare / LBare / SBare / GL) and
# every subclass of those: inherited methods, initializers and `__repr__`.
from tpy import int32


class DBare(dict[str, int32]):
    pass


class LBare(list[int32]):
    pass


class SBare(set[int32]):
    pass


class GL[U](list[U]):
    pass


class LDeep(LBare):
    pass


# a method inherited from a builtin base two classes up
def multi_builtin() -> None:
    m = LDeep()
    m.append(1)  # tpyc: ok
    print("multi-builtin", m, len(m))


class LSeeded(list[int32]):
    def __init__(self) -> None:
        super().__init__([1, 2])  # tpyc: ok


class SSeeded(set[int32]):
    def __init__(self, n: int32) -> None:
        super().__init__([n, n + 1])  # tpyc: ok


# super().__init__ with an argument runs the base's initializer
def seeded() -> None:
    print("seeded", LSeeded(), len(SSeeded(3)))


class NamedL(LBare):  # tpyc: warning(/'NamedL.__repr__' hides 'list.__repr__'/)
    def __repr__(self) -> str:
        return "Named!"


# a class with its own `__repr__` under a builtin-base ancestor prints by it,
# as an element too
def own_repr() -> None:
    n = NamedL()
    n.append(1)
    print("own-repr", n, repr(n), [n], (n,))  # tpyc: warning(/copies NamedL into owned storage/)


class LInit2(LBare):
    def __init__(self, xs: list[int32]) -> None:
        super().__init__(xs)  # tpyc: ok


class LInit3(LBare):
    def __init__(self) -> None:
        super().__init__()  # tpyc: ok


# super().__init__ through a class inheriting the builtin's constructor
# copies its argument as list.__init__ does
def seeded_multi() -> None:
    src: list[int32] = [1]
    m = LInit2(src)
    src.append(9)
    print("seeded-multi", m, LInit3(), src)


class DInit2(DBare):
    def __init__(self, d: list[tuple[str, int32]]) -> None:
        super().__init__(d)  # tpyc: ok


class SInit2(SBare):
    def __init__(self, s: set[int32]) -> None:
        super().__init__(s)  # tpyc: ok


class GLInit[U](GL[U]):
    def __init__(self, xs: list[U]) -> None:
        super().__init__(xs)  # tpyc: warning(/may copy U elements/)


# the same grandchild initializer over a dict, a set and a generic list base
def seeded_kinds() -> None:
    d = [("a", 1)]
    s = {1}
    xs = [1, 2]
    m = DInit2(d)
    t = SInit2(s)
    g = GLInit[int32](xs)
    d.append(("b", 2))
    s.add(9)
    xs.append(9)
    print("seeded-kinds", m, len(t), g, len(d), len(s), len(xs))


class SupTop(LBare):  # tpyc: warning(/'SupTop.append' hides 'list.append'/)
    def append(self, x: int32) -> None:
        super().append(x + 10)  # tpyc: ok


def main() -> None:
    multi_builtin()
    seeded()
    own_repr()
    seeded_multi()
    seeded_kinds()
