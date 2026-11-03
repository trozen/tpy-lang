# A protocol argument takes its concrete twin's form at every call kind: a storage
# Optional / union lends its borrow form, a value record at a written parameter its copy.
import asyncio
from typing import Iterator, Protocol
from tpy import Fn, Own, Stringable, ValueType, int32


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __str__(self) -> str:
        return f"A({self.n})"


class B:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __str__(self) -> str:
        return f"B({self.n})"


class H:
    o: A | None

    def __init__(self) -> None:
        self.o = A(1)

    def run(self) -> None:
        # method: the field read through self.
        print("method", text(self.o))  # tpyc: ok


class HasN(Protocol):
    def get(self) -> int32: ...


class V(ValueType):
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def get(self) -> int32:
        return self.n


class VBox:
    v: V

    def __init__(self) -> None:
        self.v = V(1)

    def run(self) -> None:
        # method: a value field read through self.
        print("written method", rebind(self.v))  # tpyc: ok

    def take(self, p: HasN) -> int32:
        return p.get() + self.v.n


class KBase:
    b: int32

    def __init__(self, b: int32) -> None:
        self.b = b


class K(KBase):
    m: int32

    def __init__(self, v: V) -> None:
        # constructor: the base call's argument and a member init.
        super().__init__(rebind(v))  # tpyc: ok
        self.m = rebind(v)  # tpyc: ok


gv = V(1)


def text(v: Stringable) -> str:
    return str(v)


# `HasN.get` is not @readonly, so each of these callees writes `p`.
def rebind(p: HasN) -> int32:
    return p.get()


def rg(p: HasN) -> int32:
    global gv
    gv = V(9)
    return p.get()


def rebind_cb(p: HasN, cb: Fn[[], None]) -> int32:
    cb()
    return p.get()


def grebind(p: HasN) -> Iterator[int32]:
    yield p.get()


async def arebind(p: HasN) -> int32:
    await asyncio.sleep(0)
    return p.get()


def gtext(v: Stringable) -> Iterator[str]:
    yield str(v)


async def atext(v: Stringable) -> str:
    await asyncio.sleep(0)
    return str(v)


def gtext_o(v: A | None) -> Iterator[str]:
    yield str(v)


def make_opt() -> Own[A | None]:
    return A(5)


def make_none() -> Own[A | None]:
    return None


def make_uni() -> Own[A | B]:
    return B(6)


def reseat(v: Stringable, h: H) -> str:
    h.o = A(9)
    return str(v)


def touch(v: Stringable, h: H) -> str:
    if h.o is not None:
        h.o.n = 7
    return str(v)


def free_field() -> None:
    h = H()
    # free function: a storage Optional field.
    print("field", text(h.o))  # tpyc: ok
    h.o = None
    print("field none", text(h.o))  # tpyc: ok


def method() -> None:
    H().run()


def loops() -> None:
    xs: list[A | None] = [A(3), None]
    for x in xs:
        # un-narrowed loop variable over a list of Optionals.
        print("loop opt", text(x))  # tpyc: ok
    us: list[A | B] = [A(4), B(5)]
    for u in us:
        # loop variable over a list of unions.
        print("loop union", text(u))  # tpyc: ok


def fresh() -> None:
    # fresh Own results at plain calls.
    print("fresh plain", text(make_opt()), text(make_none()), text(make_uni()))  # tpyc: ok
    # ... and at generator calls.
    for s in gtext(make_opt()):  # tpyc: ok
        print("fresh gen opt", s)
    for s in gtext(make_uni()):  # tpyc: ok
        print("fresh gen union", s)


async def coros() -> None:
    h = H()
    # async protocol calls: a field and a fresh result.
    print("async field", await atext(h.o))  # tpyc: ok
    print("async fresh", await atext(make_opt()), await atext(make_uni()))  # tpyc: ok
    v = V(1)
    # async call writing a value record: a local and a fresh one.
    print("written async", await arebind(v), await arebind(V(2)))  # tpyc: ok


def concrete_gen() -> None:
    # concrete generator taking a fresh Own[A | None].
    for s in gtext_o(make_opt()):  # tpyc: ok
        print("concrete gen", s)


def aliasing() -> None:
    h = H()
    h.o = None
    # the callee reseats the None field: the argument stays None.
    print("reseat", reseat(h.o, h))  # tpyc: ok
    h.o = A(1)
    # the callee mutates the engaged field's object: the argument sees it.
    print("touch", touch(h.o, h))  # tpyc: ok


def written() -> None:
    v = V(1)
    # a local value at a written parameter: the callee writes its own copy.
    print("written local", rebind(v), v.n)  # tpyc: ok
    # the callee replaces the global it was read from: the copy keeps 1.
    print("written global", rg(gv), gv.n)  # tpyc: ok
    b = VBox()
    # a value field.
    print("written field", rebind(b.v))  # tpyc: ok
    # a fresh value.
    print("written fresh", rebind(V(3)))  # tpyc: ok
    vs = [V(5), V(6)]
    # a list element: the `__getitem__` call's by-value result, copied for the callee.
    print("written element", rebind(vs[0]), vs[0].n)  # tpyc: ok
    # a record-method callee.
    print("written callee method", b.take(v))  # tpyc: ok
    k = K(v)
    # a constructor callee.
    print("written ctor", k.b, k.m)
    b.run()
    w = V(4)
    # a generator call writing a value record.
    for x in grebind(w):  # tpyc: ok
        print("written gen", x)


def written_order() -> None:
    v = V(1)

    def flip() -> V:
        nonlocal v
        v = V(9)
        return v

    # an earlier print argument rebinds it. (A protocol parameter after an
    # argument that rebinds it is BUGS.md#subexpression-right-to-left-eval.)
    print("written earlier print", flip().n, rebind(v))  # tpyc: ok
    w = V(1)

    def flip_w() -> None:
        nonlocal w
        w = V(9)

    # the callee rebinds the name through a callback before reading.
    print("written callback", rebind_cb(w, flip_w), w.n)  # tpyc: ok


def written_positions() -> None:
    v = V(1)
    i = 0
    # while condition.
    while rebind(v) + i < 3:  # tpyc: ok
        i += 1
    print("written while", i)
    # lambda body.
    print("written lambda", sorted([3, 1, 2], key=lambda x: rebind(V(x))))  # tpyc: ok
    match i:
        # match guard.
        case 2 if rebind(v) == 1:  # tpyc: ok
            print("written guard", v.n)
        case _:
            print("written no guard")
    # f-string.
    print(f"written fstr {rebind(v)}")  # tpyc: ok
    # `and` / ternary operands.
    print("written and", i > 0 and rebind(v) > 0, rebind(v) if i > 0 else 0)  # tpyc: ok


def main() -> None:
    written()
    written_order()
    written_positions()
    free_field()
    method()
    loops()
    fresh()
    asyncio.run(coros())
    concrete_gen()
    aliasing()


main()

# Module level: a global holder's field and a module-level loop variable.
gh = H()
print("module field", text(gh.o))  # tpyc: ok
gs: list[A | None] = [A(30), None]
for gx in gs:
    print("module loop", text(gx))  # tpyc: ok
# Module level: a value global at a written parameter.
print("module written", rebind(gv))  # tpyc: ok
