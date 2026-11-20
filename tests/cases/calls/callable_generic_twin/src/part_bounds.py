# A bound constrains the type an `Own[X]` type argument owns: `make -> Own[X]`
# binds `T = Own[X]`, and every bound holds of `X` -- in sema and in the C++
# concept the instantiation meets -- so the call behaves as at `T = X`.
# (The factories take an argument: a named nullary factory at `Fn[[], T]`
# is BUGS.md#fn-own-return-type-param-not-inferred.)
from __future__ import annotations
from typing import Protocol, Self
from tpy import (Comparable, ComparableRef, Default, Equatable, Fn,
                 NativeIterable, Own, ReferenceType, Sync, dispatch, int32,
                 int64)


class X:
    n: int32

    def __init__(self) -> None:
        self.n = 7

    def __lt__(self, o: X) -> bool:
        return self.n < o.n

    def __eq__(self, o: X) -> bool:
        return self.n == o.n


class Base:
    n: int32

    def __init__(self) -> None:
        self.n = 1


class Der(Base):
    def __init__(self) -> None:
        super().__init__()
        self.n = 2


class SelfEq(Protocol):
    def __eq__(self, o: Self) -> bool: ...


def make() -> Own[X]:
    return X()


def make_k(k: int32) -> Own[X]:
    return X()


def make_der(k: int32) -> Own[Der]:
    return Der()


class Source[R](Protocol):
    def get(self) -> R: ...


class S:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def get(self) -> int32:
        return self.v


def make_s(k: int32) -> Own[S]:
    return S(k)


def pull[A, R, T: Source[R]](mk: Fn[[A], T], k: A) -> R:
    s = mk(k)
    return s.get()


def make_list(k: int32) -> Own[list[int32]]:
    return [k, k + 1]


@dispatch
def classify[T: ReferenceType](f: Fn[[], T], n: int32) -> int32:
    return 1


@dispatch
def classify[T](f: Fn[[], T], n: int64) -> int32:
    return 2


def f_cmp[A, T: Comparable](mk: Fn[[A], T], k: A) -> int32:
    a = mk(k)
    b = mk(k)
    return 1 if a < b else 0


def f_cmp_ref[A, T: ComparableRef](mk: Fn[[A], T], k: A) -> int32:
    a = mk(k)
    b = mk(k)
    return 1 if a < b else 0


def f_eq[A, T: Equatable](mk: Fn[[A], T], k: A) -> int32:
    a = mk(k)
    b = mk(k)
    return 1 if a == b else 0


def f_self[A, T: SelfEq](mk: Fn[[A], T], k: A) -> int32:
    a = mk(k)
    b = mk(k)
    return 1 if a == b else 0


def f_def[A, T: Default](mk: Fn[[A], T], k: A) -> int32:
    a = mk(k)
    return 1


def f_iter[A, T: NativeIterable[int32]](mk: Fn[[A], T], k: A) -> int32:
    xs = mk(k)
    return 1


def f_base[A, T: Base](mk: Fn[[A], T], k: A) -> int32:
    a = mk(k)
    return 1


def f_sync[A, T: Sync](mk: Fn[[A], T], k: A) -> int32:
    return 1


def main() -> None:
    n: int32 = 0
    # the ReferenceType-bounded variant fits exactly; the other widens `n`
    print("bounds-ref", classify(make, n))  # tpyc: ok
    # generated protocol concepts (`Comparable`, `ComparableRef`,
    # `Equatable`, a user `Self` protocol) accept `T = Own[X]`
    print("bounds-cmp", f_cmp(make_k, 1))  # tpyc: ok
    print("bounds-cmp-ref", f_cmp_ref(make_k, 1))  # tpyc: ok
    print("bounds-eq", f_eq(make_k, 1))  # tpyc: ok
    print("bounds-self", f_self(make_k, 1))  # tpyc: ok
    # native-concept bounds are asked of the storage form too
    print("bounds-default", f_def(make_k, 1))  # tpyc: ok
    print("bounds-iter", f_iter(make_list, 3))  # tpyc: ok
    # a class bound: `Der` is a `Base`, so `Own[Der]` meets `T: Base`
    print("bounds-class", f_base(make_der, 1))  # tpyc: ok
    # a bound naming another type parameter: `R` is inferred from `S`'s
    # conformance, the type `T = Own[S]` owns
    print("bounds-assoc", pull(make_s, 4))  # tpyc: ok
    # a marker bound: `X` is Sync, so `Own[X]` meets `T: Sync`
    print("bounds-sync", f_sync(make_k, 1))  # tpyc: ok
