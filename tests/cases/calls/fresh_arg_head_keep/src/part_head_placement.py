# Where the object a `with` target read after the statement borrows is kept:
# where the target is declared, and nothing for a target that owns its value.
from typing import Iterator, Self
from tpy import Own, Ptr, int32


class Src:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class CM:
    name: str

    def __init__(self, n: int32) -> None:
        self.name = "cm-long-enough-to-heap-" + str(n) * 10

    def __enter__(self) -> Self:
        return self

    def __exit__(self, a, b, c) -> None:
        self.name += "!"


class Mid:
    cm: CM

    def __init__(self, n: int32) -> None:
        self.cm = CM(n)

    def get(self) -> CM:
        return self.cm


class NameMgr:
    name: str

    def __init__(self, n: int32) -> None:
        self.name = "manager-name-long-enough-to-heap-" + str(n) * 10

    def __enter__(self) -> Self:
        return self

    def __exit__(self, a, b, c) -> None:
        pass


def mk(n: int32) -> int32:
    return n


def nested(flag: bool) -> None:
    # a target bound again inside an `if`, read after it: the root it
    # borrows -- an owned manager, a borrowed manager's receiver -- is kept
    # in front of the `if`, where the target is declared
    with NameMgr(mk(1)) as t:  # tpyc: ok
        pass
    with Mid(1).get() as a:  # tpyc: ok
        pass
    if flag:
        with NameMgr(mk(2)) as t:  # tpyc: ok
            pass
        with Mid(2).get() as a:  # tpyc: ok
            pass
    junk = ["y" * 50 for _ in range(30)]
    print("placement nested", t.name[-3:], a.name[-3:], len(junk))


def nested_gen(flag: bool) -> Iterator[str]:
    # the same in a generator, read after a later yield: on the frame
    with NameMgr(mk(1)) as t:  # tpyc: ok
        pass
    with Mid(1).get() as a:  # tpyc: ok
        pass
    if flag:
        with NameMgr(mk(2)) as t:  # tpyc: ok
            pass
        with Mid(2).get() as a:  # tpyc: ok
            pass
    yield "a"
    junk = ["y" * 50 for _ in range(30)]
    yield t.name[-3:] + a.name[-3:] + str(len(junk))


class Box:
    v: str

    def __init__(self, v: str) -> None:
        self.v = v


class PtrMgr:
    p: Ptr[Box]

    def __init__(self, p: Ptr[Box]) -> None:
        self.p = p

    def __enter__(self) -> Self:
        return self

    def __exit__(self, a, b, c) -> None:
        pass

    def __del__(self) -> None:
        print("placement del", len(self.p.v), self.p.v == "payload-" * 8)


def destroyed_first() -> None:
    # the manager is the plain local of the scope that declares the target,
    # after the local it borrows: it goes before that local does
    b = Box("payload-" * 8)
    with PtrMgr(b) as m:  # tpyc: ok
        print("placement in", len(m.p.v))
    print("placement after", len(m.p.v))


class Holder[T]:
    v: T

    def __init__(self, v: Own[T]) -> None:
        self.v = v

    def __enter__(self) -> T:
        return self.v

    def __exit__(self, a, b, c) -> None:
        pass


class StrHolder:
    v: str

    def __init__(self, v: str) -> None:
        self.v = v

    def __enter__(self) -> str:
        return self.v

    def __exit__(self, a, b, c) -> None:
        pass


def label(s: Src) -> str:
    return "label-long-enough-to-heap-" + str(s.n) * 10


def owned_targets() -> None:
    # a copied value target keeps nothing, generic and twin alike
    with Holder(label(Src(1))) as g:  # tpyc: ok
        pass
    with StrHolder(label(Src(2))) as w:  # tpyc: ok
        pass
    print("placement owned", g[-2:], w[-2:])


def nested_def(c: bool) -> int32:
    # a nested def inside a compound statement places what its own `with`
    # keeps in its own body, not in front of the enclosing statement
    m = NameMgr(mk(0))
    if c:
        def inner(k: bool) -> int32:
            t = NameMgr(mk(4))
            if k:
                with NameMgr(mk(5)) as t:  # tpyc: ok
                    pass
            return len(t.name)
        got = inner(True)
        print("placement nested def", got)
    return len(m.name)


def main() -> None:
    nd = nested_def(True)
    print("placement nested def outer", nd)
    nested(True)
    nested(False)
    ng = list(nested_gen(True))
    print("placement nested gen", ng)
    ng_off = list(nested_gen(False))
    print("placement nested gen", ng_off)
    destroyed_first()
    owned_targets()
