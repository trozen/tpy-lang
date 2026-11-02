"""Each row is one shape `callable_fits` refuses; the accepted shapes run in
`tests/cases/calls/fn_ref_signature_fit`."""
import pytest

from tpyc.coercions import callable_fits
from tpyc.diagnostics import SemanticError
from tpyc.sema.slot_hint import SlotHint, element
from tpyc.thir.testutil import _compile
from tpyc.typesys import INT32, CallableType, PtrType, TupleType, make_fn_type

_PRELUDE = '''from typing import Any, Callable, Iterable, Iterator, Protocol
from tpy import int16, int32, int64, uint8, uint32, Fn, Own, StrView, dispatch, dynamic, readonly


class P:
    def __init__(self, v: int32) -> None:
        self.v = v


class Base:
    def __init__(self, v: int32) -> None:
        self.v = v


class Child(Base):
    def __init__(self, v: int32) -> None:
        super().__init__(v)


@dynamic
class Speaker(Protocol):
    def speak(self) -> int32: ...


class Dog(Speaker):
    def __init__(self, v: int32) -> None:
        self.v = v

    def speak(self) -> int32:
        return self.v


class OptCall:
    def __call__(self, p: P | None) -> int32:
        return 0


class TwoCalls:
    @dispatch
    def __call__(self, x: int32) -> int64:
        return 3000000000

    @dispatch
    def __call__(self, x: int64) -> int32:
        return 7


class Promoted:
    @dispatch
    def __call__(self, x: int16) -> int32:
        return 7

    @dispatch
    def __call__(self, x: int64) -> int32:
        return 3


class RoPair:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    @dispatch
    @readonly(False)
    def __call__(self, x: int32) -> int32:
        self.n += x
        return self.n

    @dispatch
    @readonly
    def __call__(self, x: int32) -> int64:
        return x


class Wide:
    @dispatch
    def __call__(self, x: int32, y: int32) -> int32:
        return x

    @dispatch
    def __call__(self, x: int32, y: int32, z: int32) -> int32:
        return x


def narrow(x: int32) -> int32:
    return x


def ret_u8(x: int32) -> uint8:
    return 1


def ret64(x: int32) -> int64:
    return x


def gen_ret[T](x: T) -> int64:
    return 7


def optp(p: P | None) -> int32:
    return 0


def u32(x: uint32) -> int32:
    return 0


def owned(s: Own[str]) -> None:
    pass


def to_float(x: float) -> float:
    return x


def mut(p: P) -> int32:
    return p.v


def un(x: int32 | str) -> int32:
    return 0


def fl_str(x: float | str) -> int32:
    return 0


def tup(t: tuple[str, int32]) -> int32:
    return t[1]


def mk(x: int32) -> Own[Dog]:
    return Dog(x)


def child(x: int32) -> Own[Child]:
    return Child(x)


def mixed(x: int32 | P) -> int32:
    return 0


def tup_or(t: tuple[P, int32] | int32) -> int32:
    return 0


def tup_opt(t: tuple[P, int32] | None) -> int32:
    return 0


def tup_take(t: tuple[P, int32]) -> int32:
    return t[1]


def ba(b: bytearray) -> int32:
    return len(b)


def anyp(x: Any) -> int32:
    return 0


def same(x: int32) -> int32:
    return x


def rp(x: int32) -> Own[P]:
    return P(x)


def rtp(x: int32) -> tuple[Own[P], int32]:
    return (P(x), x)


def t32(t: tuple[int32, int32]) -> int32:
    return t[1]


def tw_s(t: tuple[str, int64]) -> int64:
    return t[1]


def t_opt(t: tuple[str | None, int32]) -> int32:
    return t[1]


def t_un(t: tuple[int32 | str, int32]) -> int32:
    return t[1]


class Mut:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __call__(self, x: int32) -> int32:
        self.n += x
        return self.n


class Mixed:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    @dispatch
    @readonly(False)
    def __call__(self, x: int64) -> int32:
        self.n += 1
        return 64

    @dispatch
    @readonly
    def __call__(self, x: int32) -> int32:
        return 32


def call64(f: Fn[[int64], int64], s: int64) -> int64:
    return f(s)


def call32(f: Fn[[int32], int32], s: int32) -> int32:
    return f(s)


def callp(f: Fn[[P], int32], p: P) -> int32:
    return f(p)


def callu(f: Fn[[int32], int32], s: int32) -> int32:
    return f(s)


def calls(f: Fn[[str], None]) -> None:
    f("a")


def callsi(f: Fn[[str], int32]) -> int32:
    return f("a")


def callbig(f: Fn[[int], float], s: int) -> float:
    return f(s)


def callro(f: Fn[[readonly[P]], int32], p: P) -> int32:
    return f(p)


def callspk(f: Fn[[int32], Own[Speaker]]) -> int32:
    return f(1).speak()


def callbase(f: Fn[[int32], Base]) -> int32:
    return f(1).v


def calltp(f: Fn[[tuple[P, int32]], int32], p: P) -> int32:
    return f((p, 1))


def calltpown(f: Callable[[Own[tuple[P, int32]]], int32]) -> int32:
    return f((P(1), 2))


def callb(f: Fn[[bytes], int32]) -> int32:
    return f(b"ab")


def callc(f: Callable[[int32], int64]) -> int64:
    return f(1)


def callc32(f: Callable[[int32], int32]) -> int32:
    return f(1)


def fn_opt(f: Fn[[int32], int32 | None]) -> None:
    print(f(1))


def fn_un(f: Fn[[int32], int32 | str]) -> None:
    print(f(1))


def fn_wopt(f: Fn[[int32], int64 | None]) -> None:
    print(f(1))


def fn_w(f: Fn[[int32], int64]) -> None:
    print(f(1))


def c_popt(f: Callable[[int32], P | None]) -> None:
    pass


def c_tpopt(f: Callable[[int32], tuple[P, int32] | None]) -> None:
    pass


def c_ref_un(f: Callable[[int32], int32 | tuple[P, int32]]) -> None:
    pass


def c_own_un(f: Callable[[int32], tuple[Own[P], int32] | int32]) -> None:
    pass


def c_nest_un(f: Callable[[int32], tuple[tuple[P, int32], int32] | int32]) -> None:
    pass


def c_optel_un(f: Callable[[int32], tuple[P | None, int32] | int32]) -> None:
    pass


def c_val_un(f: Callable[[int32], tuple[tuple[int32, int32], int32] | int32]) -> None:
    pass


def fn_ts(f: Fn[[tuple[str, int32]], int64], t: tuple[str, int32]) -> int64:
    return f(t)


def c_ts(f: Callable[[tuple[str, int32]], int32], t: tuple[str, int32]) -> int32:
    return f(t)


def fn_ti(f: Fn[[tuple[int32, int32]], int32], t: tuple[int32, int32]) -> int32:
    return f(t)


def fn_tb(f: Fn[[tuple[int, int32]], int32], t: tuple[int, int32]) -> int32:
    return f(t)


def c_ownv_un(f: Callable[[int32], tuple[Own[int32], int32] | int32]) -> None:
    pass


def readp(p: P) -> int32:
    return p.v


def callown(f: Fn[[Own[P]], int32]) -> int32:
    return f(P(1))


def take(p: Own[P]) -> int32:
    q = [p]
    return q[0].v


def callg[T](f: Fn[[T], int32], p: T) -> int32:
    return f(p)


def my_map[T, U](f: Fn[[T], U], it: Iterable[T]) -> Iterator[U]:
    for x in it:
        yield f(x)


class H:
    p: P

    def __init__(self) -> None:
        self.p = P(1)


def get_p(h: H) -> P:
    return h.p


def get_tp(h: H) -> tuple[P, int32]:
    return (h.p, 1)


def new_p(h: H) -> Own[P]:
    return P(h.p.v)


def c_get(f: Callable[[H], P], h: H) -> int32:
    return f(h).v


def c_gett(f: Callable[[H], tuple[P, int32]], h: H) -> int32:
    return f(h)[1]


def get_op(h: H) -> P | None:
    return h.p


def c_hopt(f: Callable[[H], P | None], h: H) -> int32:
    r = f(h)
    return 0 if r is None else r.v


class GetCall:
    def __call__(self, h: H) -> P:
        return h.p


class NarrowCall:
    def __call__(self, x: int32) -> int64:
        return x


def fn_own(f: Fn[[H], Own[P]], h: H) -> int32:
    return f(h).v


def gen_it(n: int32) -> Iterator[int32]:
    for i in range(n):
        yield i


class GenCall:
    def __call__(self, n: int32) -> Iterator[int32]:
        for i in range(n):
            yield i


def c_it(f: Callable[[int32], Iterator[int32]]) -> None:
    for x in f(3):
        print(x)


def fn_it(f: Fn[[int32], Iterator[int32]]) -> None:
    for x in f(3):
        print(x)


@dispatch
def via_it(f: Fn[[int32], Iterator[int32]], n: int32) -> None:
    for x in f(n):
        print(x)


@dispatch
def via_it(f: Fn[[int32], int32], n: int32) -> None:
    print(f(n))
'''


@pytest.mark.parametrize("stmt, message", [
    # A narrower parameter would truncate what the slot passes.
    ("print(call64(narrow, 1))",
     r"'narrow' cannot be passed as Fn\[\[int64\], int64\]: its parameter 'x' "
     r"is int32 and would receive int64 \(write 'lambda x: narrow\(x\)' to "
     r"convert each call\)"),
    # A wider return.
    ("print(call32(ret64, 1))",
     r"'ret64' cannot be passed as Fn\[\[int32\], int32\]: it returns int64 "
     r"where int32 is expected"),
    # A generic function, checked after its type arguments are substituted.
    ("print(call32(gen_ret, 1))",
     r"'gen_ret' cannot be passed as Fn\[\[int32\], int32\]: it returns int64"),
    # A reference type's Optional parameter: named function, Callable value,
    # callable object.
    ("print(callp(optp, P(1)))",
     r"'optp' cannot be passed as Fn\[\[P\], int32\]: its parameter 'p' is "
     r"P \| None and would receive P"),
    ("print(callp(h, P(1)))",
     r"Type mismatch in argument 'f': expected Fn\[\[P\], int32\], got "
     r"Callable\[\[P \| None\], int32\]: its parameter 1 is P \| None and "
     r"would receive P"),
    ("print(callp(OptCall(), P(1)))",
     r"^'OptCall' cannot be passed as Fn\[\[P\], int32\] in argument 'f': "
     r"its parameter 'p' is P \| None and would receive P$"),
    # Several `__call__` variants: C++ picks the one a call through the
    # slot runs by its own rules, so none is taken. The lambda hint is given
    # only for a name.
    ("print(call32(TwoCalls(), 1))",
     r"^'TwoCalls' cannot be passed as Fn\[\[int32\], int32\] in argument "
     r"'f': it has several '__call__' variants, and a call through the slot "
     r"may run another one than a direct call \(candidates: \(int32\) -> "
     r"int64; \(int64\) -> int32\)$"),
    # A sign change is not a widening.
    ("print(callu(u32, 1))",
     r"'u32' cannot be passed as Fn\[\[int32\], int32\]: its parameter 'x' is "
     r"uint32"),
    # The owned form of a value type is a different parameter.
    ("calls(owned)",
     r"'owned' cannot be passed as Fn\[\[str\], None\]: its parameter 's' is "
     r"Own\[str\] and would receive str"),
    # BigInt -> float can raise OverflowError.
    ("print(callbig(to_float, 1))",
     r"'to_float' cannot be passed as Fn\[\[int\], float\]: its parameter 'x' "
     r"is float and would receive int"),
    # A T parameter does not receive readonly[T].
    ("print(callro(mut, P(1)))",
     r"'mut' cannot be passed as Fn\[\[readonly\[P\]\], int32\]: its "
     r"parameter 'p' is P and would receive readonly\[P\]$"),
    # A union parameter holds its members by value: a str is a view at the
    # slot, and a member is matched exactly, not widened into.
    ("print(callsi(un))",
     r"'un' cannot be passed as Fn\[\[str\], int32\]: its parameter 'x' is "
     r"int32 \| str and would receive str"),
    ("print(callu(fl_str, 1))",
     r"'fl_str' cannot be passed as Fn\[\[int32\], int32\]: its parameter 'x' "
     r"is float \| str and would receive int32"),
    # A tuple element is held by value: a view does not become a str.
    ("f: Callable[[tuple[StrView, int32]], int32] = tup",
     r"'tup' cannot be passed as Callable\[\[tuple\[StrView, int32\]\], "
     r"int32\]: its parameter 't' is tuple\[str, int32\]"),
    # A return is held by value: no subclass, no @dynamic erasure.
    ("print(callspk(mk))",
     r"'mk' cannot be passed as Fn\[\[int32\], Own\[Speaker\]\]: it returns "
     r"Own\[Dog\] where Own\[Speaker\] is expected"),
    ("print(callbase(child))",
     r"^'child' cannot be passed as Fn\[\[int32\], Base\]: it returns "
     r"Own\[Child\] where Base is expected \(write 'lambda x: child\(x\)' to "
     r"convert each call\)$"),
    ("print(call32(anyp, 1))",
     r"'anyp' cannot be passed as Fn\[\[int32\], int32\]: its parameter 'x' is "
     r"Any and would receive int32"),
    # Between two Callable values only an unchanged signature fits.
    ("w: Callable[[int64], int64] = lambda x: x\n    print(callc(w))",
     r"Type mismatch in argument 'f': expected Callable\[\[int32\], int64\], "
     r"got Callable\[\[int64\], int64\]"),
    # A borrowing parameter where the slot hands over ownership.
    ("print(callown(readp))",
     r"'readp' cannot be passed as Fn\[\[Own\[P\]\], int32\]: its parameter "
     r"'p' is P and would receive Own\[P\]"),
    # An owning parameter where the call passes the object it keeps: a
    # function parameter, a Callable, a generic twin, and a user generic
    # whose elements are not known to be handed over.
    ("print(callp(take, P(1)))",
     r"'take' cannot be passed as Fn\[\[P\], int32\]: its parameter 'p' "
     r"is Own\[P\] and would receive P \(write 'lambda p: take\(p\)'"),
    ("f: Callable[[P], int32] = take",
     r"'take' cannot be passed as Callable\[\[P\], int32\]: its parameter "
     r"'p' is Own\[P\] and would receive P"),
    ("print(callg(take, P(1)))",
     r"'take' cannot be passed as Fn\[\[P\], int32\]: its parameter 'p' "
     r"is Own\[P\] and would receive P"),
    ("print(list(my_map(take, [P(1)])))",
     r"'take' cannot be passed as Fn\[\[P\], U\]: its parameter 'p' "
     r"is Own\[P\] and would receive P"),
    # A union holding a reference member is a union of pointers at a
    # parameter: a value does not convert into it.
    ("print(call32(mixed, 1))",
     r"'mixed' cannot be passed as Fn\[\[int32\], int32\]: its parameter "
     r"'x' is P \| int32 and would receive int32"),
    # A tuple holding a reference passes as a tuple of pointers but a union
    # or Optional holds it as a tuple of objects.
    ("print(calltp(tup_or, P(1)))",
     r"'tup_or' cannot be passed as Fn\[\[tuple\[P, int32\]\], int32\]: its "
     r"parameter 't' is int32 \| tuple\[P, int32\] and would receive "
     r"tuple\[P, int32\]"),
    ("print(calltp(tup_opt, P(1)))",
     r"'tup_opt' cannot be passed as Fn\[\[tuple\[P, int32\]\], int32\]: its "
     r"parameter 't' is tuple\[P, int32\] \| None and would receive "
     r"tuple\[P, int32\]"),
    # The owned form of such a tuple is not its parameter form.
    ("print(calltpown(tup_take))",
     r"'tup_take' cannot be passed as Callable\[\[Own\[tuple\[P, int32\]\]\], "
     r"int32\]: its parameter 't' is tuple\[P, int32\] and would receive "
     r"Own\[tuple\[P, int32\]\]"),
    # The mutable sibling's parameter does not bind the owned type.
    ("print(callb(ba))",
     r"'ba' cannot be passed as Fn\[\[bytes\], int32\]: its parameter 'b' is "
     r"bytearray and would receive bytes"),
    # The hint names the argument when it is a name.
    ("pr = Promoted()\n    print(call32(pr, 1))",
     r"'Promoted' cannot be passed as Fn\[\[int32\], int32\] in argument 'f': "
     r"it has several '__call__' variants, and a call through the slot may "
     r"run another one than a direct call \(candidates: \(int16\) -> int32; "
     r"\(int64\) -> int32\) \(write 'lambda x0: pr\(x0\)' to convert each "
     r"call\)$"),
    # A mutable / @readonly pair counts as several variants, at a mutable
    # and at a readonly receiver, and at a Callable slot too.
    ("m = Mixed()\n    print(call32(m, 1))",
     r"'Mixed' cannot be passed as Fn\[\[int32\], int32\] in argument 'f': "
     r"it has several '__call__' variants.*\(write 'lambda x0: m\(x0\)'"),
    ("def ro(r: readonly[RoPair]) -> int32:\n        return call32(r, 1)\n    print(ro(RoPair()))",
     r"'RoPair' cannot be passed as Fn\[\[int32\], int32\] in argument 'f': "
     r"it has several '__call__' variants"),
    ("print(callc32(RoPair()))",
     r"'RoPair' cannot be passed as Callable\[\[int32\], int32\] in "
     r"argument 'f': it has several '__call__' variants"),
    # A readonly object whose one `__call__` mutates it, at both slot kinds.
    ("def ro(r: readonly[Mut]) -> int32:\n        return call32(r, 1)\n    print(ro(Mut()))",
     r"^'Mut' cannot be passed as Fn\[\[int32\], int32\] in argument 'f': "
     r"its '__call__' is not readonly and the object is readonly here$"),
    ("def ro(r: readonly[Mut]) -> int32:\n        return callc32(r)\n    print(ro(Mut()))",
     r"^'Mut' cannot be passed as Callable\[\[int32\], int32\] in argument "
     r"'f': its '__call__' is not readonly and the object is readonly here$"),
    # An Fn slot's return is the callable's own: no Optional or union wrap
    # and no widening into a payload.
    ("fn_opt(same)",
     r"'same' cannot be passed as Fn\[\[int32\], int32 \| None\]: it "
     r"returns int32 where int32 \| None is expected"),
    ("fn_un(same)",
     r"'same' cannot be passed as Fn\[\[int32\], int32 \| str\]: it "
     r"returns int32 where int32 \| str is expected"),
    ("fn_wopt(same)",
     r"'same' cannot be passed as Fn\[\[int32\], int64 \| None\]: it "
     r"returns int32 where int64 \| None is expected"),
    # A reference return does not convert into a Callable's Optional return.
    ("c_popt(rp)",
     r"'rp' cannot be passed as Callable\[\[int32\], P \| None\]: it "
     r"returns Own\[P\] where P \| None is expected"),
    ("c_tpopt(rtp)",
     r"'rtp' cannot be passed as Callable\[\[int32\], tuple\[P, int32\] "
     r"\| None\]: it returns tuple\[Own\[P\], int32\] where"),
    # A union is by value only when every member is, through nested
    # tuples, Optional members and Own elements.
    ("c_ref_un(same)",
     r"'same' cannot be passed as Callable\[\[int32\], int32 \| "
     r"tuple\[P, int32\]\]: it returns int32"),
    ("c_own_un(same)",
     r"'same' cannot be passed as Callable\[\[int32\], .*\]: it returns "
     r"int32"),
    ("c_nest_un(same)",
     r"'same' cannot be passed as Callable\[\[int32\], .*\]: it returns "
     r"int32"),
    ("c_optel_un(same)",
     r"'same' cannot be passed as Callable\[\[int32\], .*\]: it returns "
     r"int32"),
    # A tuple parameter converts only when both tuples hold numbers only,
    # and never wraps an element into an Optional or union.
    ("print(fn_ts(tw_s, ('abc', 1)))",
     r"'tw_s' cannot be passed as Fn\[\[tuple\[str, int32\]\], int64\]: "
     r"its parameter 't' is tuple\[str, int64\] and would receive "
     r"tuple\[str, int32\]"),
    ("print(c_ts(t_opt, ('abc', 1)))",
     r"'t_opt' cannot be passed as Callable\[\[tuple\[str, int32\]\], "
     r"int32\]: its parameter 't' is tuple\[str \| None, int32\]"),
    ("print(fn_ti(t_un, (1, 2)))",
     r"'t_un' cannot be passed as Fn\[\[tuple\[int32, int32\]\], int32\]: "
     r"its parameter 't' is tuple\[int32 \| str, int32\]"),
    # An Fn slot's return is the callable's own: a plain widening too.
    ("fn_w(ret_u8)",
     r"'ret_u8' cannot be passed as Fn\[\[int32\], int64\]: it returns "
     r"uint8 where int64 is expected"),
    # A BigInt element does not narrow into a fixed-width one.
    ("print(fn_tb(t32, (1, 2)))",
     r"'t32' cannot be passed as Fn\[\[tuple\[int, int32\]\], int32\]: "
     r"its parameter 't' is tuple\[int32, int32\] and would receive "
     r"tuple\[int, int32\]"),
    # A std::function holds its result by value: a return handed out by
    # reference or as a tuple of borrows would be copied into it. A lambda
    # calling the function copies too, so no hint is given. The documented
    # restriction: BUGS.md#callable-borrow-return-copied.
    ("print(c_get(get_p, H()))",
     r"^'get_p' cannot be passed as Callable\[\[H\], P\]: it returns a "
     r"borrow of P where the Callable keeps its own P \(return Own\[P\] if "
     r"it builds a new one\)$"),
    ("print(c_gett(get_tp, H()))",
     r"^'get_tp' cannot be passed as Callable\[\[H\], tuple\[P, "
     r"int32\]\]: it returns a borrow of tuple\[P, int32\] where the "
     r"Callable keeps its own tuple\[P, int32\] \(return "
     r"Own\[tuple\[P, int32\]\] if it builds a new one\)$"),
    # An Optional of a reference type is returned as a borrow pointer.
    ("print(c_hopt(get_op, H()))",
     r"^'get_op' cannot be passed as Callable\[\[H\], P \| None\]: it "
     r"returns a borrow of P \| None where the Callable keeps its own "
     r"P \| None"),
    # A callable object's one `__call__` is refused as a function is.
    ("print(c_get(GetCall(), H()))",
     r"^'GetCall' cannot be passed as Callable\[\[H\], P\] in argument "
     r"'f': it returns a borrow of P where the Callable keeps its own P "
     r"\(return Own\[P\] if it builds a new one\)$"),
    # One `__call__` whose parameter a direct call converts into: the hint
    # calls the object by its name.
    ("c = NarrowCall()\n    print(call64(c, 1))",
     r"^'NarrowCall' cannot be passed as Fn\[\[int64\], int64\] in argument "
     r"'f': its parameter 'x' is int32 and would receive int64 \(write "
     r"'lambda x0: c\(x0\)' to convert each call\)$"),
    # An Own return at an Fn slot says the result is owned, as a
    # Callable's does: a borrow returned there would be copied.
    ("print(fn_own(get_p, H()))",
     r"^'get_p' cannot be passed as Fn\[\[H\], Own\[P\]\]: it returns a "
     r"borrow of P where the Callable keeps its own P \(return Own\[P\] if "
     r"it builds a new one\)$"),
    # A generator, a function or an object's `__call__`, at either slot
    # kind: BUGS.md#generator-at-callable-slot-unlowered.
    ("c_it(gen_it)",
     r"^'gen_it' cannot be passed as Callable\[\[int32\], "
     r"Iterator\[int32\]\]: a generator cannot be passed as a callable yet "
     r"\(BUGS.md#generator-at-callable-slot-unlowered\)$"),
    ("fn_it(gen_it)",
     r"^'gen_it' cannot be passed as Fn\[\[int32\], Iterator\[int32\]\]: "
     r"a generator cannot be passed as a callable yet"),
    ("c_it(GenCall())",
     r"^'GenCall' cannot be passed as Callable\[\[int32\], "
     r"Iterator\[int32\]\] in argument 'f': a generator cannot be passed "
     r"as a callable yet \(BUGS.md#generator-at-callable-slot-unlowered\)$"),
    ("fn_it(GenCall())",
     r"^'GenCall' cannot be passed as Fn\[\[int32\], Iterator\[int32\]\] "
     r"in argument 'f': a generator cannot be passed as a callable yet"),
    # An overloaded callee picks the variant the generator's signature
    # fits, whose commit refuses it.
    ("via_it(gen_it, 3)",
     r"^'gen_it' cannot be passed as Fn\[\[int32\], Iterator\[int32\]\]: "
     r"a generator cannot be passed as a callable yet"),
    # The hint's parameter names skip the object's own name.
    ("x0 = Promoted()\n    print(call32(x0, 1))",
     r"several '__call__' variants.*\(write 'lambda x1: x0\(x1\)' to "
     r"convert each call\)$"),
    # No variant has the slot's arity: the hint still names one parameter
    # per slot parameter.
    ("w = Wide()\n    print(call32(w, 1))",
     r"'Wide' cannot be passed as Fn\[\[int32\], int32\] in argument 'f': "
     r"it has several '__call__' variants.*\(write 'lambda x0: w\(x0\)' "
     r"to convert each call\)$"),
], ids=["param-narrowing", "return-narrowing", "generic-ref",
        "optional-ref-named", "optional-ref-value", "optional-ref-object",
        "object-selected-overload", "sign-change", "own-str", "bigint-float",
        "readonly-into-mutable", "str-into-union", "int-widened-into-union",
        "tuple-view-element", "own-dynamic-return", "subclass-return", "any",
        "callable-to-callable-widened", "borrow-at-own-slot",
        "own-at-borrow-slot", "own-at-callable", "own-at-generic-slot",
        "own-at-user-generic-feed", "value-into-pointer-union",
        "ref-tuple-into-union", "ref-tuple-into-optional",
        "own-ref-tuple", "bytes-into-bytearray",
        "object-converted-overloads", "object-mutable-readonly-pair",
        "object-readonly-pair-at-readonly", "object-readonly-pair-at-callable",
        "readonly-object-mutating-call-fn",
        "readonly-object-mutating-call-callable", "fn-return-optional",
        "fn-return-union", "fn-return-widened-optional",
        "ref-return-optional", "ref-tuple-return-optional",
        "ref-tuple-member-union", "own-element-union", "nested-ref-union",
        "optional-element-union", "tuple-str-widened",
        "tuple-element-optional", "tuple-element-union",
        "fn-return-widened", "tuple-bigint-narrowed",
        "borrow-return-at-callable", "borrow-tuple-return-at-callable",
        "borrow-optional-return-at-callable", "borrow-return-object",
        "object-hint-by-name", "borrow-return-at-own-fn",
        "generator-function-at-callable", "generator-function-at-fn",
        "generator-object-at-callable", "generator-object-at-fn",
        "generator-at-overloaded-callee", "object-hint-skips-own-name", "object-hint-no-arity-match"])
def test_refused_shape(stmt: str, message: str) -> None:
    source = (_PRELUDE + "\n\ndef main() -> None:\n"
              "    h: Callable[[P | None], int32] = optp\n"
              f"    {stmt}\n")
    with pytest.raises(SemanticError, match=message):
        _compile(source)


def test_enclosing_type_param_is_no_wildcard() -> None:
    """A type parameter of the function being analyzed is a type there,
    not one a call is inferring: `narrow` would truncate an int64 `T`."""
    source = (_PRELUDE + "\n\ndef use[T](x: T) -> T:\n"
              "    g: Callable[[T], T] = narrow\n"
              "    return g(x)\n\n\n"
              "def main() -> None:\n"
              "    print(use(int64(3000000000)))\n")
    with pytest.raises(SemanticError, match=(
            r"'narrow' cannot be passed as Callable\[\[T\], T\]: its "
            r"parameter 'x' is int32 and would receive T")):
        _compile(source)


def test_by_value_union_takes_member() -> None:
    """A union whose every member is held by value, nested tuples
    included, takes a by-value member at a Callable return."""
    _compile(_PRELUDE + "\n\ndef main() -> None:\n    c_val_un(same)\n")


def test_by_value_union_takes_own_value_element() -> None:
    """An `Own` on a value element of a tuple member leaves the union by
    value."""
    _compile(_PRELUDE + "\n\ndef main() -> None:\n    c_ownv_un(same)\n")


def test_owned_return_at_callable() -> None:
    """An owned return is the value a Callable slot holds, and an Fn slot
    returning `Own`; at a plain Fn slot a borrowing return is the
    callable's own."""
    _compile(_PRELUDE + "\n\ndef main() -> None:\n"
             "    print(c_get(new_p, H()))\n"
             "    print(callh(get_p, H()))\n"
             "    print(fn_own(new_p, H()))\n"
             "\n\ndef callh(f: Fn[[H], P], h: H) -> int32:\n"
             "    return f(h).v\n")


def test_zero_parameter_lambda_hint() -> None:
    """The lambda hint for a callable of no parameters has no blank."""
    source = (_PRELUDE + "\n\ndef mk() -> int64:\n    return 1\n\n\n"
              "def run(f: Fn[[], int32]) -> int32:\n    return f()\n\n\n"
              "def main() -> None:\n    print(run(mk))\n")
    with pytest.raises(SemanticError, match=(
            r"'mk' cannot be passed as Fn\[\[\], int32\]: it returns int64 "
            r"where int32 is expected \(write 'lambda: mk\(\)' to convert "
            r"each call\)$")):
        _compile(source)


def test_fn_value_at_callable() -> None:
    """An `Fn` parameter's callable returns its own form, which may be a
    borrow (`get_p` fits there): passed on to a `Callable` it would be
    copied, and the remedy is the parameter's own spelling."""
    source = (_PRELUDE + "\n\ndef fn_to_c(f: Fn[[H], P], h: H) -> int32:\n"
              "    return c_get(f, h)\n\n\n"
              "def main() -> None:\n    print(fn_to_c(get_p, H()))\n")
    with pytest.raises(SemanticError, match=(
            r"^Type mismatch in argument 'f': expected Callable\[\[H\], P\], "
            r"got Fn\[\[H\], P\]: it returns a borrow of P where the "
            r"Callable keeps its own P \(spell the parameter "
            r"Fn\[\[H\], Own\[P\]\]\)$")):
        _compile(source)


def test_async_at_unowned_callable_has_no_lambda_hint() -> None:
    """An async def at a `Callable` slot whose return is the coroutine
    handle without `Own` differs only by ownership: a lambda returns the
    same handle, so none is offered."""
    source = ("import asyncio\n"
              "from typing import Callable\n"
              "from tpy import int32\n"
              "from tpy.coro import Cancellable\n\n\n"
              "async def double(n: int32) -> int32:\n"
              "    await asyncio.sleep(0.0)\n"
              "    return n + n\n\n\n"
              "async def run(f: Callable[[int32], Cancellable[int32]]) -> int32:\n"
              "    return await f(3)\n\n\n"
              "async def main_coro() -> int32:\n"
              "    return await run(double)\n\n\n"
              "print(asyncio.run(main_coro()))\n")
    with pytest.raises(SemanticError, match=(
            r"^'double' cannot be passed as Callable\[\[int32\], "
            r"Cancellable\[int32\]\]: it returns Own\[Cancellable\[int32\]\] "
            r"where Cancellable\[int32\] is expected$")):
        _compile(source)


def test_ptr_tuple_return_is_a_value() -> None:
    """A `Ptr` is returned as it is stored, so a function returning a tuple
    of one fits a `Callable` holding that tuple (a record pointee's
    `return (p, 1)` does not lower yet, so the accepted run is the scalar
    `Ptr` section of fn_ref_signature_fit;
    BUGS.md#ptr-tuple-element-rejects)."""
    ret = TupleType((PtrType(INT32), INT32))
    assert callable_fits(make_fn_type((INT32,), ret),
                         CallableType((INT32,), ret),
                         inherits=lambda _a, _b: False)


def test_projection_keeps_only_a_fit_binding() -> None:
    """A part of a fill-only hint types no argument node, so `map` drops
    the binding, as it did before the binding carried a callable fit; a
    callable-fit binding stays bound to its node. (No compiled shape maps
    a fill-only hint today: a filled literal is analyzed under
    `as_local`.)"""
    node = object()
    pair = TupleType((INT32, INT32))
    part = SlotHint.fill(pair, node).map(element(0))
    assert part is not None and part.binding is None
    fit = SlotHint.declared(pair).fitting(node, frozenset({"T"}), frozenset())
    part = fit.map(element(0))
    assert part is not None and part.callable_fit_at(node).open_params == {"T"}
