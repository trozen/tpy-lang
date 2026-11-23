# Element-copying builtins copy where CPython shares the elements, silently until their
# signatures spell it (BUGS.md#list-copy-concat-elements-copy-unwarned); pop's `default: Own[V]` warns.
from tpy import int32, uint64, copy, Own
from part_sets import main as sets_main
from part_positions import main as positions_main
from part_concat import main as concat_main
from part_cpy_copy import main as cpy_copy_main
from part_subclass import main as subclass_main


class A:
    def __init__(self, v: int32) -> None:
        self.v = v


def first(xs: list[A]) -> int32:
    return xs[0].v


def mk() -> Own[list[A]]:
    return [A(5)]


def lists() -> None:
    xs = [A(1), A(2), A(3)]
    zs = [A(4)]
    # The receiver's elements are copied into the fresh list, silently.
    ys = xs.copy()  # tpyc: ok
    # Both operands of `+` are copied, silently.
    both = xs + zs  # tpyc: ok
    # A stepped slice copies the receiver's elements, silently.
    every = first(xs[::2])  # tpyc: ok
    print("lists:", len(ys), len(both), every)
    # The spelled copies match CPython's: the source is untouched.
    cp = copy(xs)
    cp[0].v = 7
    plus = copy(xs) + copy(zs)
    plus[0].v = 8
    print("lists: source", xs[0].v, len(plus))
    # Fresh operands are no copy CPython could observe.
    fresh = mk() + mk()  # tpyc: ok
    print("lists: fresh", fresh[0].v)


class K:
    def __init__(self, k: int32) -> None:
        self.k = k

    def __hash__(self) -> uint64:
        return hash(self.k)

    def __eq__(self, o: "K") -> bool:
        return self.k == o.k


def dicts() -> None:
    k0 = K(1)
    d: dict[K, A] = {K(0): A(0)}
    # the literal copies the key it is given
    e = {k0: A(1)}  # tpyc: warning(/copies K into owned storage/)
    # Keys and values of the argument are copied into the receiver: the
    # `Own[V]` values warn, the keys are silent.
    d.update(e)  # tpyc: warning(/copies A/)
    a = A(9)
    # The default is an owning slot (`default: Own[V]`): a live default is copied, warned.
    v = d.pop(K(5), a)  # tpyc: warning(/copies A into owned storage/)
    print("dicts:", len(d), v.v)
    d2: dict[K, A] = {}
    d2.update(copy(e))
    print("dicts: key shared", any(k is k0 for k in d2))
    w = d.pop(K(6), copy(a))
    w.v = 3
    print("dicts: default", a.v)


def gc[T](xs: list[T]) -> Own[list[T]]:
    # A generic body copies whatever T is, silently.
    return xs.copy()  # tpyc: ok


def generic() -> None:
    xs = [A(1)]
    ys = gc(xs)
    ns = gc([1, 2])
    print("generic:", len(ys), len(ns))


class C:
    def __init__(self, v: int32) -> None:
        self.v = v

    def __copy__(self) -> Own["C"]:
        return C(self.v + 100)


def hooks() -> None:
    # copy() of a container copies each element through its __copy__.
    cs = [C(1)]
    print("hooks:", copy(cs)[0].v, copy({1: C(2)})[1].v)


# module level: a global receiver
GX = [A(1), A(2)]
GY = GX.copy()  # tpyc: ok
print("module:", len(GY))


def main() -> None:
    lists()
    hooks()
    dicts()
    generic()
    sets_main()
    positions_main()
    concat_main()
    cpy_copy_main()
    subclass_main()


main()
