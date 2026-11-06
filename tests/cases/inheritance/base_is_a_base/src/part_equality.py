# `==` / `!=`, `in`, count and index on builtin-base subclasses and plain
# containers: overriding `__eq__`, generic subclasses, mixed needles.
from tpy import int32, Array, Equatable, readonly
from part_init import GL


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


def main() -> None:
    eq_override()
    plain_equatable()
    generic_eq()
    ys: list[int32] = [1]
    print("ro-eq", ro_eq(ys, ys))
    union_count()
    bytearray_noinit()
    cross_needle()
    literal_args()
    explicit_contains()
