# `in` on a readonly dict/set haystack takes the hashed `__contains__` (a dict
# tests its KEYS) at every readonly position; a readonly list keeps the scan.
from typing import Iterator
from tpy import readonly, Hashable, copy, int32


# Free function: the dict's value type need not be Equatable -- `in` tests keys.
def has_key(d: readonly[dict[str, list[int32]]], k: str) -> bool:
    return k in d  # tpyc: ok


def lacks(s: readonly[set[int32]], k: int32) -> bool:
    return k not in s  # tpyc: ok


def in_list(xs: readonly[list[int32]], k: int32) -> bool:
    return k in xs


# Free generic function: an open-T needle into the hashed lookup.
def gfind[T: Hashable](s: readonly[set[T]], v: T) -> bool:
    return v in s  # tpyc: ok


class Bag[K: Hashable, V]:
    _data: dict[K, V]

    def __init__(self) -> None:
        self._data = {}

    # Method: `__contains__` is readonly, so `self._data` is a readonly dict.
    def __contains__(self, key: K) -> bool:
        return key in self._data  # tpyc: ok

    # Generic class method, inferred readonly: the same lookup.
    def has(self, key: K) -> bool:
        return key in self._data  # tpyc: ok

    def add(self, key: K, v: V) -> None:
        if key not in self._data:
            self._data[key] = copy(v)


class Pos:
    # A user class's readonly `__contains__` on a readonly receiver.
    def __contains__(self, k: int32) -> bool:
        return k > 0


def user_recv(p: readonly[Pos]) -> bool:
    return 3 in p  # tpyc: ok


# Generator: the membership inside a resumable body.
def each(d: readonly[dict[str, int32]], ks: list[str]) -> Iterator[bool]:
    for k in ks:
        yield k in d  # tpyc: ok


# Closure: the readonly haystack captured by a nested function.
def via_closure(d: readonly[dict[str, int32]]) -> bool:
    def inner(k: str) -> bool:
        return k in d  # tpyc: ok
    return inner("a") and not inner("z")


# Alias: a local bound to a readonly param stays readonly.
def via_alias(d: readonly[dict[str, int32]]) -> bool:
    e = d
    return "a" in e  # tpyc: ok


def main() -> None:
    d: dict[str, list[int32]] = {"a": [1]}
    print("free:", has_key(d, "a"), has_key(d, "b"))
    s = {1, 2}
    print("free set:", lacks(s, 1), lacks(s, 3))
    xs: list[int32] = [1, 2]
    print("free list:", in_list(xs, 2), in_list(xs, 5))
    print("generic fn:", gfind(s, 2), gfind(s, 7))
    b: Bag[str, int32] = Bag()
    b.add("x", 1)
    b.add("x", 2)
    print("method:", "x" in b, "y" in b)
    print("generic method:", b.has("x"), b.has("y"))
    print("user recv:", user_recv(Pos()))
    c = {"a": 1}
    for r in each(c, ["a", "z"]):
        print("generator:", r)
    print("closure:", via_closure(c))
    print("alias:", via_alias(c))


main()
