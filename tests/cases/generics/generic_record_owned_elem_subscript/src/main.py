# A generic record constructed from a subscript of a generic list[T]/dict[K, V]
# binds its type argument to the storage form, not the element borrow form.
# Covers a bare ctor param (V[U] with x: U), an Own[T] ctor param, a tuple
# element (the borrow nests inside the tuple), and a dict-value subscript. All
# must infer the value type, never Ref[...]. The records are reference types
# moved into via the constructor; the program reads the stored values back.
from tpy import Own, copy


class Bare[U]:
    x: U

    def __init__(self, x: U) -> None:
        self.x = x


class Owned[T]:
    v: T

    def __init__(self, v: Own[T]) -> None:
        self.v = v


class Pair[A, B]:
    p: tuple[A, B]

    def __init__(self, p: Own[tuple[A, B]]) -> None:
        self.p = p


def use_bare[T](src: list[T]) -> Own[Bare[T]]:
    b = Bare(src[0])  # tpyc: type(/Bare\[T\]/)
    return b


def use_owned[T](src: list[T]) -> Own[Owned[T]]:
    o = Owned(copy(src[0]))  # tpyc: type(/Owned\[T\]/)
    return o


def use_pair[T](src: list[tuple[T, int]]) -> Own[Pair[T, int]]:
    p = Pair(copy(src[0]))  # tpyc: type(/Pair\[T, int\]/)
    return p


def use_dict[K, V](src: dict[K, V], k: K) -> Own[Owned[V]]:
    o = Owned(copy(src[k]))  # tpyc: type(/Owned\[V\]/)
    return o


def main() -> None:
    nums: list[int] = [7, 8]
    print(use_bare(nums).x)
    print(use_owned(nums).v)
    rows: list[tuple[int, int]] = [(1, 2), (3, 4)]
    pair = use_pair(rows)
    print(pair.p[0], pair.p[1])
    table: dict[str, int] = {"a": 9}
    print(use_dict(table, "a").v)


main()
