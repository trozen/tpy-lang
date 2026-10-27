# A subclass is its base whatever the base is: a user generic or concrete
# record, a multi-level chain, or a builtin dict / list / set -- at a param,
# isinstance, a class pattern, an Optional / union, a bound, readonly -- and
# every inherited method, dunder and initializer resolves through the MRO.
import asyncio
from asyncio import Future
from typing import Any, Iterable, Iterator, Protocol, override
from tpy import int32, Array, Equatable, Hashable, dynamic, readonly


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


class DSub(dict[str, int32]):
    def __init__(self) -> None:
        pass


class LSub(list[int32]):
    def __init__(self) -> None:
        pass


class SSub(set[int32]):
    def __init__(self) -> None:
        pass


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


def put_dict(m: dict[str, int32], k: str) -> int32:
    m[k] = 7
    return len(m)


def put_list(xs: list[int32]) -> int32:
    xs.append(9)
    return len(xs)


def put_set(xs: set[int32]) -> int32:
    xs.add(9)
    return len(xs)


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


@dynamic
class Speaker(Protocol):
    def speak(self) -> str: ...


class Animal:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def speak(self) -> str:
        return "..."


class Dog(Animal):  # tpyc: warning(/hides 'Animal.speak'/)
    def __init__(self, name: str) -> None:
        super().__init__(name)

    def speak(self) -> str:
        return "woof"


class Quiet(Animal):
    def __init__(self, name: str) -> None:
        super().__init__(name)

    def nap(self) -> str:
        return "zz"


class Pet(Speaker):
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def speak(self) -> str:
        return "..."


class Cat(Pet):
    def __init__(self, name: str) -> None:
        super().__init__(name)

    def speak(self) -> str:
        return "meow"


# Overriding a dict method is a hide: a dict reference runs dict's.
class Counter(dict[str, int32]):  # tpyc: warning(/'Counter.__getitem__' hides 'dict.__getitem__'/)
    def __init__(self) -> None:
        pass

    def __getitem__(self, k: str) -> int32:
        return 0


def animal_name(a: Animal) -> str:
    return a.name


def pet_name(p: Pet) -> str:
    return p.name


def dict_size(m: dict[str, int32]) -> int32:
    return len(m)


# static dispatch through a base param: warned only where an override is lost
def dispatch() -> None:
    print("dispatch", animal_name(Dog("rex")))  # tpyc: warning(/'Dog' passed as 'Animal': calls through 'a' run 'Animal.speak', not 'Dog.speak'/)
    print("dispatch", animal_name(Quiet("q")))  # tpyc: ok
    print("dispatch", pet_name(Cat("tom")))  # tpyc: ok
    c = Counter()
    print("dispatch", dict_size(c))  # tpyc: warning(/'Counter' passed as 'dict\[str, int32\]': calls through 'm' run 'dict.__getitem__', not 'Counter.__getitem__'/)


class DInit(dict[str, int32]):
    def __init__(self) -> None:
        super().__init__()  # tpyc: ok


class LInit(list[int32]):
    def __init__(self) -> None:
        super().__init__()  # tpyc: ok


class SInit(set[int32]):
    def __init__(self) -> None:
        super().__init__()  # tpyc: ok


class DBare(dict[str, int32]):
    pass


class LBare(list[int32]):
    pass


class SBare(set[int32]):
    pass


# A generic builtin subclass writes and tests itself through the inherited
# dunders.
class GDict[K: Hashable, V](dict[K, V]):
    def __init__(self) -> None:
        super().__init__()

    def put(self, k: K, v: V) -> bool:
        self[k] = v  # tpyc: warning(/may copy V/)
        return k in self  # tpyc: ok


def total(it: Iterable[int32]) -> int32:
    n = 0
    for x in it:
        n += x
    return n


# membership: the inherited `__contains__` (dict, set) or iteration (list)
def membership() -> None:
    d = DSub()
    put_dict(d, "a")
    xs = LSub()
    put_list(xs)
    ss = SSub()
    put_set(ss)
    print("in", "a" in d, "b" not in d, 9 in xs, 5 in xs, 9 in ss, 4 not in ss)  # tpyc: ok


# iteration over each subclass, after a write through a base-typed param
def iteration() -> None:
    d = DInit()
    put_dict(d, "k")
    xs = LInit()
    put_list(xs)
    ss = SInit()
    put_set(ss)
    out = ""
    for k in d:  # tpyc: ok
        out += k
    n = 0
    for x in xs:  # tpyc: ok
        n += x
    for y in ss:  # tpyc: ok
        n += y
    print("iter", out, n, total(xs), total(ss))


# a base-typed local aliases the subclass object
def alias() -> None:
    d = DSub()
    m: dict[str, int32] = d  # tpyc: warning(/upcast narrows 'DSub'/)
    m["x"] = 1
    xs = LSub()
    l: list[int32] = xs  # tpyc: warning(/upcast narrows 'LSub'/)
    l.append(4)
    ss = SSub()
    s: set[int32] = ss  # tpyc: warning(/upcast narrows 'SSub'/)
    s.add(6)
    print("alias", d["x"], xs[0], 6 in ss)


# method: a generic builtin / user-base subclass through its own dunders
def generic_builtin() -> None:
    g: GDict[str, int32] = GDict()
    print("generic", g.put("a", 1), put_dict(g, "b"), len(g), g["b"])  # tpyc: ok
    u: GSub[str, int32] = GSub()
    u.put("u", 4)
    print("generic-user", u["u"], len(u))


# super().__init__() to a builtin base, and an inherited constructor
def constructors() -> None:
    d = DBare()  # tpyc: ok
    d["a"] = 1
    print("ctor", len(DInit()), len(LInit()), len(SInit()), len(d), len(LBare()), len(SBare()))


# print and == follow the builtin base
def printing() -> None:
    d = DBare()
    d["a"] = 1
    e = DBare()
    e["a"] = 1
    p: dict[str, int32] = {"a": 1}
    xs = LBare()
    xs.append(2)
    ss = SBare()
    ss.add(3)
    print("print", d, xs, ss, SBare())  # tpyc: ok
    print("eq", d == e, d != p, p == d, xs == LBare(), ss == SBare())  # tpyc: ok
    # an element prints through the inherited `__repr__` too; the list
    # literal is an owning slot, so it copies the record as for any class
    print("print-nested", [d], (ss, xs))  # tpyc: warning(/copies DBare into owned storage/)
    # str() / repr() / an f-string reach the inherited `__repr__`
    print("print-str", str(d), repr(ss), f"{xs}")  # tpyc: ok
    ys: list[int32] = [2]
    # a plain container compares with a subclass through its own `__eq__`
    print("eq-mixed", ys == xs, xs != ys, ys != xs)  # tpyc: ok


class EqL(list[int32]):  # tpyc: warning(/'EqL.__eq__' hides 'list.__eq__'/)
    def __init__(self) -> None:
        pass

    def __eq__(self, other: "EqL") -> bool:
        return False


class EqD(dict[str, int32]):  # tpyc: warning(/'EqD.__eq__' hides 'dict.__eq__'/) warning(/'EqD.__repr__' hides 'dict.__repr__'/)
    def __init__(self) -> None:
        pass

    def __eq__(self, other: "EqD") -> bool:
        return True

    def __repr__(self) -> str:
        return "EqD!"


class EqD2(EqD):
    pass


# `!=` runs the `__ne__` the MRO resolves (the base's), not a negated `__eq__`
def eq_override() -> None:
    a = EqL()
    b = EqL()
    # spurious: list.__ne__ never calls EqL.__eq__
    # (BUGS.md#builtin-dunder-arg-warning-false-positive)
    print("eq-override", a == b, a != b)  # tpyc: warning(/'EqL' passed as 'list.int32.'/)
    x = EqD2()
    y = EqD2()
    x["k"] = 1
    # spurious (BUGS.md#builtin-dunder-arg-warning-false-positive); the list
    # literal copies x into its owning slot
    print("eq-override-dict", x == y, x != y, x, [x])  # tpyc: warning(/'EqD2' passed as 'dict.str, int32.'/) warning(/copies EqD2 into owned storage/)


# plain containers are Equatable when their elements are: a container
# needle in a list, a container argument to `index` / `count`
def plain_equatable() -> None:
    nested = [[1], [2]]
    ds = [{"x": 1}]
    sets: list[set[int32]] = [{1}]
    k: list[int32] = [2]
    # an element of an owning list literal copies the named list
    lol: list[list[int32]] = [[1], k]  # tpyc: warning(/copies list.int32. into owned storage/)
    print("plain-eq", [2] in nested, {"x": 1} in ds, {1} in sets,  # tpyc: ok
          lol.index([2]), lol.count(k), k in lol)  # tpyc: ok


class GL[U](list[U]):
    pass


class GLT[T](list[T]):
    pass


class EqGL[T](list[T]):  # tpyc: warning(/'EqGL.__eq__' hides 'list.__eq__'/)
    def __eq__(self, other: "EqGL[T]") -> bool:
        return False


def ne_generic[U: Equatable](a: list[U], b: list[U]) -> bool:
    # an open element type resolves `list.__ne__` as a concrete one does
    return a != b  # tpyc: ok


# a generic subclass compares by its base's dunders at every instantiation,
# whatever its parameter is named; Array elements compare as list elements
def generic_eq() -> None:
    a = GL[int32]()
    b = GL[int32]()
    a.append(1)
    b.append(1)
    print("generic-eq", a == b, a != b)  # tpyc: ok
    p: list[int32] = []
    t = GLT[int32]()
    print("generic-mixed", p == t, t == p)  # tpyc: ok
    e = EqGL[int32]()
    f = EqGL[int32]()
    # spurious (BUGS.md#builtin-dunder-arg-warning-false-positive)
    print("generic-override", e == f, e != f)  # tpyc: warning(/'EqGL.int32.' passed as 'list.int32.'/)
    k: list[int32] = [1]
    print("generic-fn", ne_generic(k, p), ne_generic(k, k))
    aa: list[Array[int32, 2]] = [[1, 2]]
    ab: list[Array[int32, 2]] = [[1, 2]]
    da: dict[str, Array[int32, 2]] = {"a": [1, 2]}
    db: dict[str, Array[int32, 2]] = {"a": [3, 4]}
    print("array-elems", aa == ab, aa != ab, da == db, da != db)  # tpyc: ok


class Pt:
    def __init__(self, x: int32) -> None:
        self.x = x

    def __eq__(self, other: "Pt") -> bool:
        return self.x == other.x

    def __hash__(self) -> int:
        return hash(self.x)


class NoPt(Pt):  # tpyc: warning(/'NoPt.__eq__' hides 'Pt.__eq__'/) warning(/'NoPt.__hash__' hides 'Pt.__hash__'/)
    def __eq__(self, other: "Pt") -> bool:
        return False

    def __hash__(self) -> int:
        return 999


# Never run: a builtin's method compares through its parameter's static
# type, so a subclass overriding `__eq__` there warns at the argument.
def native_callees(xs: list[Pt], s: set[Pt], m: dict[Pt, int32],
                   nested: list[list[int32]], plain: list[int32]) -> None:
    n = NoPt(1)
    print(xs.count(n))  # tpyc: warning(/calls through parameter 'value' of list.count run 'Pt.__eq__'/)
    s.discard(n)  # tpyc: warning(/calls through parameter 'elem' of set.discard/)
    print(m.get(n, -1))  # tpyc: warning(/calls through parameter 'key' of dict.get/)
    sub = EqL()
    print(nested.count(sub))  # tpyc: warning(/'EqL' passed as 'list.int32.': calls through parameter 'value' of list.count/)
    # Python asks the subclass's reflected `__eq__` first; TPy runs list's
    print(plain == sub)  # tpyc: warning(/calls through parameter 'other' of list.__eq__/)


def gen_keys(d: DSub) -> Iterator[str]:
    for k in d:
        if k in d:  # tpyc: ok
            yield k


# generator, comprehension and closure positions
def positions() -> None:
    d = DSub()
    put_dict(d, "p")
    xs = LSub()
    put_list(xs)
    keys = ""
    for k in gen_keys(d):
        keys += k
    sq = [x * 2 for x in xs if x in xs]  # tpyc: ok

    def has(k: str) -> bool:
        return k in d  # tpyc: ok

    print("positions", keys, sq, has("p"), has("q"))


class AppError(Exception):
    def __init__(self, msg: str) -> None:
        super().__init__(msg)


def log(e: Exception) -> str:
    return "logged"


# except: a caught builtin / user exception binds an Exception parameter
def exceptions() -> None:
    try:
        raise ValueError("bad")
    except ValueError as e:
        print("exc", log(e))  # tpyc: ok
    try:
        raise AppError("app")
    except AppError as e:
        print("exc", log(e))  # tpyc: ok


class BSub(bytearray):
    def __init__(self) -> None:
        pass


# a bytearray subclass prints by its base's rule
def bytearray_print() -> None:
    b = BSub()
    b.append(65)
    print("bytearray", b)  # tpyc: ok


class Shelter:
    name: str

    def __init__(self, a: Animal) -> None:
        self.name = a.name

    def admit(self, a: Animal) -> str:
        return a.name


# the lost-override warning at a method argument and a constructor argument
def dispatch_sites() -> None:
    d = Dog("rex")
    s = Shelter(d)  # tpyc: warning(/'Dog' passed as 'Animal': calls through parameter 'a' of Shelter.__init__ run 'Animal.speak'/)
    print("dispatch-sites", s.name, s.admit(d))  # tpyc: warning(/'Dog' passed as 'Animal'/)


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


class NamedL(LBare):  # tpyc: warning(/'NamedL.__repr__' hides 'list.__repr__'/)
    def __repr__(self) -> str:
        return "Named!"


# a class with its own `__repr__` under a builtin-base ancestor prints by it,
# as an element too
def own_repr() -> None:
    n = NamedL()
    n.append(1)
    print("own-repr", n, repr(n), [n], (n,))  # tpyc: warning(/copies NamedL into owned storage/)


# an Optional subclass is truthy by the dunder its MRO resolves
def opt_truth(o: LBare | None, h: HasLen | None, s: LenSub | None) -> None:
    print("opt-truth", "y" if o else "n", "y" if h else "n", "y" if s else "n", not o, not s)  # tpyc: ok


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


class FL(list[float]):
    pass


# a needle of another numeric type than the elements, at a generic and a
# concrete list subclass; `in` over a bytearray subclass and a bytearray
def cross_needle() -> None:
    g = GL[int32]()
    g.append(1)
    f = FL()
    f.append(1.5)
    b = BNo()
    b.append(66)
    ba = bytearray(b"hi")
    print("cross-needle", 1.0 in g, 2.5 in g, 1.5 in f, 2 in f)  # tpyc: ok
    print("bytearray-in", 66 in b, 67 in b, 104 in ba, 105 not in ba, b"h" in ba)  # tpyc: ok


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


# the explicit `__contains__` call on a generic list subclass compares, as `in` does
def explicit_contains() -> None:
    g = GL[int32]()
    g.append(1)
    print("explicit-contains", g.__contains__(1.0), g.__contains__(2.5), 1.0 in g)  # tpyc: ok


class Pr:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __repr__(self) -> str:
        return f"Pr({self.n})"


# a list literal argument to a runtime callee spells its type
def literal_args() -> None:
    d: dict[str, list[int32]] = {"a": [1]}
    p = Pr(1)
    x = 1
    s = "a"
    nested: list[list[int32]] = [[1], [2]]
    print("literal-args", d.get("x", [2]), d.pop("x", [3]), f"{[p]}")  # tpyc: ok
    print("literal-in", x in [1, 2], 3 in [1, 2], s in ["a", "b"], "c" in ["a", "b"], nested.index([2]))  # tpyc: ok


# a container's `__eq__` / `__ne__` take the other side readonly
def ro_eq(a: readonly[list[int32]], b: readonly[list[int32]]) -> bool:
    return a.__eq__(b) and a == b and not a.__ne__(b)  # tpyc: ok


@dynamic
class Meter(Protocol):
    def m(self) -> int32: ...


class SpA(Meter):
    def m(self) -> int32:
        return 1


class SpB(SpA):
    @override
    def m(self) -> int32:  # tpyc: ok
        return 2


def via_spa(a: SpA) -> int32:
    return a.m()


# @override of an inherited @dynamic protocol member dispatches at run time
# and does not warn
def dyn_override() -> None:
    print("dyn-override", via_spa(SpB()))  # tpyc: ok


# a union, an Optional and None compare part-wise at count / index
def union_count() -> None:
    xs: list[int32 | str] = [1, "a"]
    u: int32 | str = "a"
    os: list[int32 | None] = [1, None]
    print("union-count", xs.count(u), xs.index(u), os.count(None))  # tpyc: ok


class BNo(bytearray):
    pass


# a bytearray subclass with no `__init__`
def bytearray_noinit() -> None:
    b = BNo()
    b.append(66)
    print("bytearray-noinit", len(b), b[0])


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
    d.grow()  # tpyc: warning(/Mutation of 'd' while borrowed .'grow' may invalidate/)
    a.n = 3


class SupL(list[int32]):  # tpyc: warning(/'SupL.append' hides/) warning(/'SupL.__eq__' hides/)
    def append(self, x: int32) -> None:
        super().append(x * 2)  # tpyc: ok

    @readonly
    def size(self) -> int32:
        return super().__len__()  # tpyc: ok

    def __eq__(self, other: "SupL") -> bool:
        # spurious (BUGS.md#builtin-dunder-arg-warning-false-positive)
        return super().__eq__(other)  # tpyc: warning(/'SupL' passed as 'list.int32.'/)

    def twice(self) -> Iterator[int32]:
        yield super().__len__()  # tpyc: ok


class SupD(dict[str, int32]):  # tpyc: warning(/'SupD.__setitem__' hides/)
    def __setitem__(self, k: str, v: int32) -> None:
        super().__setitem__(k, v + 1)  # tpyc: ok


class SupM(SupL):  # tpyc: warning(/'SupM.append' hides 'SupL.append'/)
    def append(self, x: int32) -> None:
        super().append(x + 1)  # tpyc: ok


class SupTop(LBare):  # tpyc: warning(/'SupTop.append' hides 'list.append'/)
    def append(self, x: int32) -> None:
        super().append(x + 10)  # tpyc: ok


class SupU(list[int32]):  # tpyc: warning(/'SupU.append' hides/)
    def append(self, x: int32) -> None:
        list.append(self, x + 100)  # tpyc: ok


class SupBag[T](list[T]):  # tpyc: warning(/'SupBag.append' hides/)
    def append(self, x: T) -> None:
        super().append(x)  # tpyc: warning(/may copy T/)


class SupP:
    def __init__(self, v: int32) -> None:
        self.v = v

    def __add__(self, o: int32) -> int32:
        return self.v + o


class SupQ(SupP):  # tpyc: warning(/'SupQ.__add__' hides 'SupP.__add__'/)
    def __add__(self, o: int32) -> int32:
        return super().__add__(o) * 10  # tpyc: ok


# super().m(...) / Base.m(self, ...) to a builtin base's method runs it on
# the base part of self, at a plain, readonly, dunder and generator body
def super_builtin() -> None:
    a = SupL()
    a.append(3)
    b = SupL()
    b.append(3)
    print("super-append", a[0], len(a), a.size(), list(a.twice()))
    print("super-eq", a == b)
    b.append(1)
    print("super-eq-after", a == b, len(b))
    d = SupD()
    d["k"] = 4
    print("super-setitem", d["k"], len(d))
    m = SupM()
    m.append(5)
    t = SupTop()
    t.append(1)
    print("super-two-level", m[0], len(m), t[0], len(t))
    u = SupU()
    u.append(1)
    g = SupBag[int32]()
    g.append(7)
    print("super-unbound", u[0], len(u), "super-generic", g[0], len(g))
    q = SupQ(1)
    print("super-user-dunder", q + 2)


@dynamic
class SupAddable(Protocol):
    def __add__(self, o: int32) -> int32: ...


class SupVBase(SupAddable):
    def __add__(self, o: int32) -> int32:
        return o + 1

    def plus(self, o: int32) -> int32:
        return o + 2


class SupVChild(SupVBase):
    @override
    def __add__(self, o: int32) -> int32:
        # runs the base's body, not this override again (a polymorphic base)
        return super().__add__(o) * 10  # tpyc: ok

    def vals(self) -> Iterator[int32]:
        # in a generator frame the base's body runs on the stored receiver
        yield super().__add__(2)  # tpyc: ok
        yield SupVBase.__add__(self, 3)  # tpyc: ok
        yield super().plus(4)  # tpyc: ok

    async def aval(self) -> int32:
        await asyncio.sleep(0)
        # in an async frame, after a suspension, likewise
        a = super().__add__(5)  # tpyc: ok
        return a + SupVBase.__add__(self, 6)  # tpyc: ok


class ClrL(list[int32]):
    def empty(self) -> int32:
        super().clear()  # tpyc: ok
        return len(self)


class ClrD(dict[str, int32]):
    def empty(self) -> int32:
        super().clear()  # tpyc: ok
        return len(self)


class ClrS(set[int32]):
    def empty(self) -> int32:
        super().clear()  # tpyc: ok
        return len(self)

    def empty_unbound(self) -> int32:
        set.clear(self)  # tpyc: ok
        return len(self)


class SupBox:
    def __init__(self, n: int32) -> None:
        self.n = n


class SupBoxes(list[SupBox]):
    def first(self) -> SupBox:
        # a borrow of self's element: the method stays mutable
        return super().__getitem__(0)  # tpyc: ok

    def first_unbound(self) -> SupBox:
        return list.__getitem__(self, 0)  # tpyc: ok

    def first_n(self) -> int32:
        return self[0].n

    def pin_first(self) -> None:
        # the super() borrow held in a local through self: a write through it
        # reaches self's element
        x = self.first()
        x.n = 9  # tpyc: ok
        print("super-borrow-self", self[0].n)


# super() to a base's dunder through a polymorphic base; a body-less base
# method (`clear`) on the base part of self; a base method's borrow of self
def super_base_part() -> None:
    v = SupVChild()
    print("super-virtual-dunder", v + 2)
    print("super-frame-gen", list(v.vals()))
    print("super-frame-async", asyncio.run(v.aval()))
    l = ClrL()
    l.append(1)
    d = ClrD()
    d["a"] = 1
    s = ClrS()
    s.add(1)
    s2 = ClrS()
    s2.add(2)
    print("super-clear", l.empty(), len(l), d.empty(), len(d), s.empty(),
          len(s), s2.empty_unbound(), len(s2))
    bs = SupBoxes()
    bs.append(SupBox(4))
    bs.first().n = 5
    a = bs.first_n()
    bs.first_unbound().n = 6
    print("super-borrow", a, bs.first_n())
    bs.pin_first()


class ChooseBase:
    def choose[U](self, value: U) -> U:
        return value


class ChooseChild[U](ChooseBase):
    pass


# a generic method of a non-generic base keeps its own type parameter under a
# subclass parameter of the same name
def nongeneric_base_method() -> None:
    print("nongeneric-base", ChooseChild[int32]().choose(1.5))  # tpyc: ok


class StaticG[T]:
    @staticmethod
    def ident(x: T) -> T:
        return x

    @staticmethod
    def pick[U](x: U) -> U:
        return x


class StaticPlain:
    @staticmethod
    def pick[U](x: U) -> U:
        return x


class StaticPlainSub[U](StaticPlain):
    pass


class StaticConcSub[U](StaticG[int32]):
    pass


class FutSub[U](Future[U]):
    pass


class FutConc(Future[int32]):
    pass


async def awaited_poll() -> None:
    f = FutSub[int32]()
    f.set_result(7)
    g = FutConc()
    g.set_result(8)
    # `await` reads the __poll__ the subclass inherits, at its binding.
    print("inherited-await", await f, await g)  # tpyc: ok


class StaticSub(StaticG[int32]):
    pass


class StaticGS[U](StaticG[U]):
    pass


def inherited_signatures() -> None:
    dd = DBare()
    dd["b"] = 1
    dd["a"] = 2
    ll = LBare()
    ll.append(3)
    ll.append(1)
    # A protocol bound's type argument read off the inherited dict / list methods.
    ks = sorted(dd)  # tpyc: ok
    es = sorted(ll)  # tpyc: ok
    dd["c"] = 3
    ll.append(0)
    print("inherited-sorted", ks, es)
    for e in reversed(ll):  # tpyc: ok
        print("inherited-reversed", e)
    names = list(dd)  # tpyc: ok
    names.append("z")
    print("inherited-list", names, len(dd))
    # An inherited staticmethod reads its signature at the subclass's binding.
    print("inherited-static", StaticSub.ident(4) + 1)  # tpyc: ok
    print("inherited-static-generic", StaticGS[int32].ident(6) + 1)  # tpyc: ok
    # The method's own type parameter is not captured by the subclass's.
    print("inherited-static-own", StaticGS[int32].pick(1.5),  # tpyc: ok
          StaticConcSub[int32].pick(2.5), StaticPlainSub[int32].pick(3.5))
    asyncio.run(awaited_poll())


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
    dispatch()
    membership()
    iteration()
    alias()
    generic_builtin()
    constructors()
    printing()
    eq_override()
    plain_equatable()
    generic_eq()
    positions()
    exceptions()
    bytearray_print()
    dispatch_sites()
    iter_override()
    truthiness()
    multi_builtin()
    seeded()
    iter_override_consumers()
    own_repr()
    opt_truth(LBare(), HasLen(), LenSub())
    seeded_multi()
    ys: list[int32] = [1]
    print("ro-eq", ro_eq(ys, ys))
    dyn_override()
    union_count()
    bytearray_noinit()
    seeded_kinds()
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
    cross_needle()
    literal_args()
    empty_method()
    explicit_contains()
    super_builtin()
    super_base_part()
    nongeneric_base_method()
    inherited_signatures()


main()
