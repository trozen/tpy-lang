# An owning slot of a base type (a field, a container element) copies a
# subclass object's base part with the copy warning, a builtin dict / list /
# set base as a record base: the copy is independent of the object.
from tpy import int32


class DSub(dict[str, int32]):
    pass


class LSub(list[int32]):
    pass


class SSub(set[int32]):
    pass


class Base:
    n: int32

    def __init__(self) -> None:
        self.n = 0


class Sub(Base):
    def __init__(self) -> None:
        super().__init__()


class Holder:
    m: dict[str, int32]
    l: list[int32]
    s: set[int32]
    b: Base

    def __init__(self) -> None:
        self.m = {}
        self.l = []
        self.s = set()
        self.b = Base()

    def take(self, d: DSub) -> None:
        self.m = d  # tpyc: warning(/upcast narrows 'DSub'/) warning(/copies DSub into field/)


def main() -> None:
    h = Holder()
    # field and element stores of a dict subclass
    d = DSub()
    d["a"] = 1
    h.m = d  # tpyc: warning(/upcast narrows 'DSub'/) warning(/copies DSub into field/)
    h.m["b"] = 2
    ms: list[dict[str, int32]] = []
    ms.append(d)  # tpyc: warning(/copies DSub into owned storage/)
    ms[0]["c"] = 3
    print("dict", len(d), len(h.m), len(ms[0]))
    # ... of a list subclass
    xs = LSub()
    xs.append(1)
    h.l = xs  # tpyc: warning(/upcast narrows 'LSub'/) warning(/copies LSub into field/)
    h.l.append(2)
    ls: list[list[int32]] = []
    ls.append(xs)  # tpyc: warning(/copies LSub into owned storage/)
    ls[0].append(3)
    print("list", len(xs), len(h.l), len(ls[0]))
    # ... of a set subclass
    ss = SSub()
    ss.add(1)
    h.s = ss  # tpyc: warning(/upcast narrows 'SSub'/) warning(/copies SSub into field/)
    h.s.add(2)
    sl: list[set[int32]] = []
    sl.append(ss)  # tpyc: warning(/copies SSub into owned storage/)
    sl[0].add(3)
    print("set", len(ss), len(h.s), len(sl[0]))
    # ... of a record subclass
    s = Sub()
    h.b = s  # tpyc: warning(/upcast narrows 'Sub'/) warning(/copies Sub into field/)
    h.b.n = 2
    bs: list[Base] = []
    bs.append(s)  # tpyc: warning(/copies Sub into owned storage/)
    bs[0].n = 3
    print("record", s.n, h.b.n, bs[0].n)
    # method: a field store from a parameter
    d2 = DSub()
    h.take(d2)
    h.m["z"] = 1
    print("method", len(d2), len(h.m))


main()
