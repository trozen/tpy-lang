# An overriding `__iter__` drives every consumer; truthiness runs the
# nearest `__bool__` / `__len__`; growing an inherited container warns.
from typing import Any, Iterator, override
from tpy import int32, readonly
from fixtures import total
from part_init import DBare, LBare, SBare


class Sevens(list[int32]):  # tpyc: warning(/hides 'list.__iter__'/)
    def __init__(self) -> None:
        pass

    def __iter__(self) -> Iterator[int32]:
        yield 7


# a subclass overriding `__iter__` is iterated through it everywhere
def iter_override() -> None:
    xs = Sevens()
    xs.append(1)
    n = 0
    for x in xs:  # tpyc: ok
        n += x
    print("iter-override", n, total(xs), [y for y in xs], sum(xs), set(xs), list(xs),  # tpyc: ok
          sorted(xs), max(xs))


class HasLen:
    def __init__(self) -> None:
        pass

    def __len__(self) -> int32:
        return 0


class LenSub(HasLen):
    pass


# truthiness runs the nearest `__bool__` / `__len__`, record and builtin bases alike
def truthiness() -> None:
    x = LBare()
    r = LenSub()
    d = DBare()
    print("truth", not x, bool(x), not r, bool(r), not d, "y" if x else "n")  # tpyc: ok
    x.append(1)
    while x:  # tpyc: ok
        print("truth-while", len(x))
        break
    print("truth-filter", [y for y in [1, 2] if x])


class P:
    def __init__(self, n: int32) -> None:
        self.n = n


class PList(list[P]):
    pass


# Never run: growing a list subclass while iterating it or borrowing an
# element invalidates the iterator / the element, warned as on `list[P]`.
def invalidation(l: PList) -> None:
    for p in l:
        l.append(P(p.n))  # tpyc: warning(/while iterating over it .'append' invalidates/)
    e = l[0]
    l.append(P(2))  # tpyc: warning(/while borrowed .'append' may invalidate/)
    e.n = 1


class SevStr(list[str]):  # tpyc: warning(/hides 'list.__iter__'/)
    def __init__(self) -> None:
        pass

    def __iter__(self) -> Iterator[str]:
        yield "s"


class SevSet(set[int32]):  # tpyc: warning(/hides 'set.__iter__'/)
    def __init__(self) -> None:
        pass

    def __iter__(self) -> Iterator[int32]:
        yield 7


class SevDict(dict[str, int32]):  # tpyc: warning(/hides 'dict.__iter__'/)
    def __init__(self) -> None:
        pass

    def __iter__(self) -> Iterator[str]:
        yield "k"


# an overriding `__iter__` keeps the base's `in` (list.__contains__ does not
# iterate) and drives enumerate / zip / filter / extend / join / list()
def iter_override_consumers() -> None:
    xs = Sevens()
    xs.append(1)
    print("in-override", 1 in xs, 7 in xs, 7 not in xs)  # tpyc: ok
    print("iter-builtins", list(enumerate(xs)), list(zip(xs, xs)), list(filter(None, xs)))  # tpyc: ok
    w: list[int32] = []
    w.extend(xs)  # tpyc: ok
    ss = SevStr()
    ss.append("a")
    s = SevSet()
    s.add(1)
    d = SevDict()
    d["a"] = 1
    print("iter-consumers", w, "-".join(ss), sorted(s), list(d), 1 in s, "a" in d)  # tpyc: ok


# an Optional subclass is truthy by the dunder its MRO resolves
def opt_truth(o: LBare | None, h: HasLen | None, s: LenSub | None) -> None:
    print("opt-truth", "y" if o else "n", "y" if h else "n", "y" if s else "n", not o, not s)  # tpyc: ok


# an Optional set / dict subclass is truthy by its length: None, empty, non-empty
def opt_truth_kinds(s: SBare | None, d: DBare | None) -> None:
    print("opt-kinds", "y" if s else "n", "y" if d else "n", not s, not d)  # tpyc: ok


# a readonly dict / set compares by its base's `==` / `!=`
def ro_eq_kinds(a: readonly[dict[str, int32]], b: readonly[dict[str, int32]],
                s: readonly[set[int32]], t: readonly[set[int32]]) -> None:
    print("ro-eq-kinds", a == b, a != b, s == t, s != t)  # tpyc: ok


class OvL(list[int32]):
    @override
    def __len__(self) -> int32:  # tpyc: warning(/'OvL.__len__' overrides 'list.__len__' but the override is non-polymorphic/)
        return 3


class Yes(list[int32]):
    def __bool__(self) -> bool:
        return True


# a container element's truth is its own `__bool__` over its base's length,
# at all / any / filter(None, ..) as at `if`
def truth_consumers() -> None:
    ys = [Yes()]
    ss = [SBare(), SBare()]
    ss[1].add(1)
    print("truth-consumers", all(ys), any(ys), list(filter(None, ys)),  # tpyc: warning(/copies Yes elements/)
          len(list(filter(None, ss))), len(OvL()))  # tpyc: warning(/copies SBare elements/)


class Emp:
    def empty(self) -> bool:
        return True


class EmpStr:
    def empty(self) -> str:
        return "yes"


# an object with an `empty()` method but no `__bool__` / `__len__` is truthy,
# as every object without them is
def empty_method() -> None:
    es = [Emp()]
    a: Any = EmpStr()
    print("empty-method", len(list(filter(None, es))), "y" if Emp() else "n", "y" if a else "n")  # tpyc: warning(/copies Emp elements/)


class Grow:
    ps: list[P]

    def __init__(self) -> None:
        self.ps = [P(1)]

    def at(self, i: int32) -> P:
        return self.ps[i]

    def grow(self) -> None:
        self.ps.append(P(2))


class GrowSub(Grow):
    pass


# Never run: an inherited user method growing its receiver invalidates a
# borrowed element, warned as on the base
def invalidation_user(d: GrowSub) -> None:
    a = d.at(0)
    d.grow()  # tpyc: warning(/Mutation of 'd' while borrowed .'grow' may invalidate/) warning(/may move the elements of 'd.ps'/)
    a.n = 3


def main() -> None:
    iter_override()
    truthiness()
    iter_override_consumers()
    opt_truth(LBare(), HasLen(), LenSub())
    opt_truth(None, None, None)
    full = LBare()
    full.append(1)
    opt_truth(full, HasLen(), LenSub())
    opt_truth_kinds(None, None)
    opt_truth_kinds(SBare(), DBare())
    s1 = SBare()
    s1.add(1)
    d1 = DBare()
    d1["a"] = 1
    opt_truth_kinds(s1, d1)
    ro_eq_kinds(d1, {"a": 1}, s1, {2})
    truth_consumers()
    empty_method()
