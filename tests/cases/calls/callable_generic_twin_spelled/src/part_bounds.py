# Every bound is asked of the type a spelled `Own[X]` type argument owns: a
# bound naming another type parameter, a class-shadowed method bound, the
# warned copy a reference-bounded `-> T` makes at `T = Own[P]`; and the
# owned-result tie-break between a user lending and building `@dispatch`
# pair (compiler-only: CPython's stand-in runs the first variant).
from tpy import Fn, Own, ReferenceType, dispatch, int32


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def make(k: int32) -> Own[P]:
    return P(k)


def dep[T, U: T](x: U) -> int32:
    return 1


class C[T]:
    def __init__(self) -> None:
        pass

    def test[T: ReferenceType](self) -> int32:
        return 1


def pick[A, T: ReferenceType](f: Fn[[A], T], k: A, d: T) -> T:
    return d


@dispatch
def fold[T: ReferenceType](func: Fn[[T, T], T], a: list[T]) -> T:
    print("fold-lend")
    acc = a[0]
    for i in range(1, len(a)):
        acc = func(acc, a[i])
    return acc


@dispatch
def fold[T](func: Fn[[T, T], Own[T]], a: list[T]) -> Own[T]:
    print("fold-build")
    acc = func(a[0], a[1])
    for i in range(2, len(a)):
        acc = func(acc, a[i])
    return acc


def keep[T](x: T) -> T:
    return x


class Q:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def __eq__(self, o: Q) -> bool:
        return o.x == self.x


def collect[T](x: T) -> int32:
    xs = list[T]()
    xs.append(x)
    return len(xs)


class D[T]:
    def __init__(self) -> None:
        pass

    @staticmethod
    def first(xs: list[T]) -> T:
        return xs[0]


def own_add2(a: Own[P], b: Own[P]) -> Own[P]:
    return P(a.x + b.x)


def bigger(a: P, b: P) -> P:
    return a if a.x >= b.x else b


def main() -> None:
    # `U: T` at `T = U = Own[P]`: the substituted bound owns `P` as well
    print("bounds-dep", dep[Own[P], Own[P]](P(1)))  # tpyc: ok
    # a class-shadowed method bound at the receiver `C[P]` (a class type
    # argument is never `Own[X]` until unit 2b: BUGS.md#class-own-type-arg-method-result-ill-formed)
    c = C[P]()
    print("bounds-class", c.test())  # tpyc: ok
    # `T = Own[P]` owns: `d` copies into the result (warned), so the write
    # through `r` leaves `d` alone, where CPython's `r` is `d`
    d = P(2)
    r = pick(make, 1, d)  # tpyc: warning(/copies P into owned storage/)
    r.x = 5
    print("bounds-pick", d.x, r.x)
    # both variants fit `own_add2`; the one writing `Own[T]` at the owned
    # result wins the tie
    f = fold(own_add2, [P(1), P(2), P(3)])  # tpyc: warning(/copies P into owned storage/)
    print("bounds-fold-own", f.x)
    # a lending combiner still lends: the write reaches the list's element
    qs = [P(1), P(2)]
    fold(bigger, qs).x += 100  # tpyc: ok
    print("bounds-fold-lend", qs[1].x)
    # `-> T` at `T = Own[P]` hands the temporary over: nothing borrows it
    k = keep[Own[P]](P(7))  # tpyc: ok
    print("bounds-keep", k.x)
    # a builtin `list[T]` at `T = Own[P]` owns its element either way: no
    # class type argument, so nothing is refused
    # (calls/error_class_own_type_arg_substituted holds `C[T]` instead)
    print("bounds-list-own", collect[Own[P]](P(2)))  # tpyc: ok
    # a class-shadowed method bound at a builtin receiver `list[Own[Q]]`:
    # `count[T: Equatable]` is asked of `Q`
    oq = list[Own[Q]]()
    oq.append(Q(1))
    print("bounds-list-count", oq.count(Q(1)))  # tpyc: ok
    # a static method spelled at the class's `T = Own[P]`: the class type
    # drops the `Own` (warned), so the result lends as at `D[P]`
    # (BUGS.md#class-own-type-arg-method-result-ill-formed)
    ds = [P(1)]
    dh = D.first[Own[P]](ds)  # tpyc: warning(/`Own\[P\]` in a type argument of class 'D' has no effect yet; write `D\[P\]`/)
    dh.x = 9
    print("bounds-static-spelled", ds[0].x)
