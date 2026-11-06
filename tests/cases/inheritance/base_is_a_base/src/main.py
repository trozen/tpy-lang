# A subclass is its base whatever the base is: a user generic or concrete
# record, a multi-level chain, or a builtin dict / list / set -- at a param,
# isinstance, a class pattern, an Optional / union, a bound, readonly -- and
# every inherited method, dunder and initializer resolves through the MRO.
from typing import Iterator
from tpy import int32, Hashable, readonly
from fixtures import DSub, LSub, SSub, put_dict, put_list, put_set
import part_dispatch
import part_containers
import part_init
import part_equality
import part_iter_truth
import part_super
import part_signatures


class Base[K: Hashable, V]:
    _d: dict[K, V]

    def __init__(self) -> None:
        self._d = {}

    def __iter__(self) -> Iterator[K]:
        for k in self._d:
            yield k

    def __len__(self) -> int32:
        return len(self._d)

    def __setitem__(self, k: K, v: V) -> None:
        # A generic V may be a reference type, whose store copies.
        self._d[k] = v  # tpyc: warning(/may copy V/)

    def __getitem__(self, k: K) -> V:
        return self._d[k]


class GSub[K: Hashable, V](Base[K, V]):
    def __init__(self) -> None:
        super().__init__()

    def put(self, k: K, v: V) -> None:
        self[k] = v  # tpyc: warning(/may copy V/)


class CSub(Base[str, int32]):
    def __init__(self) -> None:
        super().__init__()


class CSub2(CSub):
    def __init__(self) -> None:
        super().__init__()


def as_base(s: CSub) -> Base[str, int32]:
    return s  # tpyc: warning(/upcast narrows 'CSub' to 'Base.str, int32.'/)


def as_dict(d: DSub) -> dict[str, int32]:
    return d  # tpyc: warning(/upcast narrows 'DSub' to 'dict.str, int32.'/)


# return as base: a borrowed subclass comes back as a base reference to it
def returns() -> None:
    s = CSub()
    b = as_base(s)
    b["r"] = 5
    d = DSub()
    m = as_dict(d)
    m["r"] = 6
    print("return", len(s), s["r"], len(d), d["r"])


def put_base(m: Base[str, int32], k: str) -> int32:
    m[k] = 7
    return len(m)


def keys(m: Base[str, int32]) -> str:
    out = ""
    for k in m:
        out += k
    return out


# user generic base: Sub[str, int32] at a Base[str, int32] param aliases it
def gen() -> None:
    s: GSub[str, int32] = GSub()
    s["a"] = 1
    n = put_base(s, "g")  # tpyc: ok
    print("gen", n, s["g"], keys(s), isinstance(s, Base), isinstance(s, GSub))


# concrete base: Sub(Base[str, int32])
def conc() -> None:
    s = CSub()
    s["a"] = 1
    n = put_base(s, "c")  # tpyc: ok
    print("conc", n, s["c"], isinstance(s, Base), isinstance(s, CSub))


# a base-typed local aliases the subclass object
def local() -> None:
    s: GSub[str, int32] = GSub()
    b: Base[str, int32] = s  # tpyc: warning(/upcast narrows 'GSub.str, int32.' to 'Base.str, int32.'/)
    b["l"] = 8
    print("local", len(s), s["l"])


# multi-level: CSub2(CSub(Base[str, int32]))
def multi() -> None:
    s = CSub2()
    n = put_base(s, "m")  # tpyc: ok
    print("multi", n, s["m"], isinstance(s, Base), isinstance(s, CSub))


# builtin bases: the subclass binds the base-typed param by reference
def builtin() -> None:
    d = DSub()
    d["a"] = 1
    n = put_dict(d, "d")  # tpyc: ok
    out = ""
    for k in d:  # inherited dict iteration
        out += k
    print("dict", n, d["d"], len(d), out)
    xs = LSub()
    xs.append(1)
    print("list", put_list(xs), xs[1], len(xs))  # tpyc: ok
    ss = SSub()
    ss.add(1)
    print("set", put_set(ss), len(ss))  # tpyc: ok


# isinstance: a subclass is an instance of every base, and of no sibling
def isinst() -> None:
    d = DSub()
    xs = LSub()
    plain: dict[str, int32] = {}
    lit = [1]
    print("isinstance", isinstance(d, dict), isinstance(d, DSub), isinstance(d, list),  # tpyc: ok
          isinstance(xs, list), isinstance(plain, dict), isinstance(lit, list))
    print("isinstance-desc", desc(plain))


def desc(m: dict[str, int32]) -> bool:
    return isinstance(m, DSub)  # tpyc: warning(/descendant type 'DSub'/)


# match: a base's class pattern matches the subclass subject
def matching() -> None:
    s = CSub()
    match s:
        case Base():  # tpyc: ok
            print("match base")
    d = DSub()
    match d:
        case dict():  # tpyc: ok
            print("match dict")


def opt_len(m: Base[str, int32] | None) -> int32:
    if m is None:
        return -1
    m["o"] = 3
    return len(m)


# Optional[Sub] at an Optional[Base] param
def optional() -> None:
    s: CSub | None = CSub()
    print("optional", opt_len(s), opt_len(None))  # tpyc: ok
    if s is not None:
        print("optional-after", s["o"])


# union member: isinstance against a base selects the subclass member
def union_member(v: CSub | int32) -> None:
    if isinstance(v, Base):  # tpyc: ok
        print("union", put_base(v, "u"), v["u"])
    else:
        print("union int", v)


# A class bound gives the body no members (as for any class bound); the
# call site checks it.
def bounded[T: Base[str, int32]](m: T) -> int32:
    return 1


def bounded_dict[T: dict[str, int32]](m: T) -> int32:
    return 2


def ro_len(m: readonly[dict[str, int32]]) -> int32:
    return len(m)


# a subclass satisfies a base bound; readonly base param
def bounds() -> None:
    s = CSub()
    d = DSub()
    d["a"] = 1
    print("bound", bounded(s), bounded_dict(d), ro_len(d))  # tpyc: ok


# A generic builtin subclass writes and tests itself through the inherited
# dunders.
class GDict[K: Hashable, V](dict[K, V]):
    def __init__(self) -> None:
        super().__init__()

    def put(self, k: K, v: V) -> bool:
        self[k] = v  # tpyc: warning(/may copy V/)
        return k in self  # tpyc: ok


# method: a generic builtin / user-base subclass through its own dunders
def generic_builtin() -> None:
    g: GDict[str, int32] = GDict()
    print("generic", g.put("a", 1), put_dict(g, "b"), len(g), g["b"])  # tpyc: ok
    u: GSub[str, int32] = GSub()
    u.put("u", 4)
    print("generic-user", u["u"], len(u))


def main() -> None:
    gen()
    conc()
    local()
    multi()
    builtin()
    isinst()
    matching()
    optional()
    cs = CSub()
    union_member(cs)
    k = 5
    union_member(k)
    bounds()
    returns()
    generic_builtin()
    part_dispatch.main()
    part_containers.main()
    part_init.main()
    part_equality.main()
    part_iter_truth.main()
    part_super.main()
    part_signatures.main()


main()
