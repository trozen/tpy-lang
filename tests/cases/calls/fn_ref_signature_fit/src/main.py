# A callable fits a callback parameter when C++ receives each value unchanged (docs/LANGUAGE_FEATURES.md "Lambda / Closures", Callable -> Fn).
import asyncio
from typing import Callable, Protocol
from tpy import int32, int64, uint8, float32, Fn, Own, Ptr, StrView, String, dispatch, dynamic, readonly
from tpy.coro import Cancellable
from helper import h_wide


class P:
    def __init__(self, v: int32) -> None:
        self.v = v


class Base:
    def __init__(self, v: int32) -> None:
        self.v = v

    @readonly
    def get(self) -> int32:
        return self.v


class HasV(Protocol):
    @readonly
    def get(self) -> int32: ...


class Child(Base):
    def __init__(self, v: int32) -> None:
        super().__init__(v)


# Kid overrides get so a T bound to Base instead of Kid shows in the output;
# the override's static-dispatch note is expected.
class Kid(Base):  # tpyc: warning(/hides 'Base.get'/)
    def __init__(self, v: int32) -> None:
        super().__init__(v)

    @readonly
    def get(self) -> int32:
        return self.v * 100


@dynamic
class Speaker(Protocol):
    @readonly
    def speak(self) -> int32: ...


class Dog(Speaker):
    def __init__(self, v: int32) -> None:
        self.v = v

    @readonly
    def speak(self) -> int32:
        return self.v


def wide(x: int64) -> int64:
    return x + 1


def big(x: int) -> int:
    return x * 1000000000000


def half(x: float) -> float:
    return x / 2


def gen_wide[T](x: T) -> int64:
    return 7


def count(p: readonly[P]) -> int32:
    return p.v


def maybe(x: int32 | None) -> int64:
    return -1 if x is None else x * 2


def maybe_s(s: str | None) -> int32:
    return -1 if s is None else len(s)


def ret8(x: int32) -> uint8:
    return 200


def shout(x: int32) -> int32:
    print("void slot saw", x)
    return x


def getv(b: Base) -> int32:
    return b.v


def ask(s: readonly[Speaker]) -> int32:
    return s.speak()


class Pet(Speaker):
    def __init__(self, v: int32) -> None:
        self.v = v

    @readonly
    def speak(self) -> int32:
        return self.v


class Puppy(Pet):
    def __init__(self, v: int32) -> None:
        super().__init__(v)

    @readonly
    def speak(self) -> int32:
        return self.v * 10


def pet_speak(p: readonly[Pet]) -> int32:
    return p.speak()


def tag[T: HasV](x: T) -> int32:
    return x.get()


def sv(s: StrView) -> int32:
    return len(s)


def st(s: str) -> int32:
    return len(s)


def u(x: int32 | str) -> str:
    if isinstance(x, str):
        return x
    return "int " + str(x)


def tw(t: tuple[int64, int64]) -> int64:
    return t[0] + t[1]


def cb(x: int32) -> int32:
    return x


class Wide:
    def __call__(self, x: int64) -> int64:
        return x * 3


class Offset:
    def __init__(self, k: int64) -> None:
        self.k = k

    @readonly
    def __call__(self, x: int32) -> int64:
        return x + self.k


class Scale:
    def __init__(self, k: int64) -> None:
        self.k = k

    def __call__(self, x: int32) -> int64:
        return x * self.k


def twb(t: tuple[int, int32]) -> int64:
    return t[1] * 10


def own_inc(x: Own[int32]) -> int64:
    return x + 1


def own_big(x: Own[int]) -> int:
    x += 1
    return x


def own_wide(x: Own[int64]) -> int64:
    x += 1
    return x


def same(x: int32) -> int32:
    return x


def getp(p: P) -> int32:
    return p.v


def maybe_cb(f: Callable[[P], int32] | None) -> int32:
    return 0 if f is None else f(P(7))


def maybe64(x: int64 | None) -> int64:
    return -1 if x is None else x * 3


def blen(b: bytes) -> int32:
    return len(b)


def call32(f: Fn[[int32], int64], s: int32) -> int64:
    return f(s)


def callc32(f: Callable[[int32], int64], s: int32) -> int64:
    return f(s)


def callbig(f: Fn[[int32], int], s: int32) -> int:
    return f(s)


def callbb(f: Fn[[int], int], s: int) -> int:
    return f(s)


def callcb(f: Fn[[Callable[[P], int32]], int32], g: Callable[[P], int32]) -> int32:
    return f(g)


def callf(f: Fn[[float32], float], s: float32) -> float:
    return f(s)


def callfi(f: Fn[[int32], float], s: int32) -> float:
    return f(s)


def callp(f: Fn[[P], int32], p: P) -> int32:
    return f(p)


def callv(f: Fn[[int32], None], s: int32) -> None:
    f(s)


def call_child(f: Fn[[Child], int32], c: Child) -> int32:
    return f(c)


def call_kid(f: Fn[[Kid], int32], k: Kid) -> int32:
    return f(k)


def call_dog(f: Fn[[readonly[Dog]], int32], d: Dog) -> int32:
    return f(d)


def call_puppy(f: Fn[[readonly[Puppy]], int32], p: Puppy) -> int32:
    return f(p)


def call_str(f: Fn[[str], int32], s: str) -> int32:
    return f(s)


def call_string(f: Fn[[String], int32], s: String) -> int32:
    return f(s)


def call_u(f: Fn[[int32], str], x: int32) -> str:
    return f(x)


def call_tw(f: Fn[[tuple[int32, int32]], int64], t: tuple[int32, int32]) -> int64:
    return f(t)


def call_opt(f: Callable[[int32], int32 | None], s: int32) -> int32 | None:
    return f(s)


def call_ustr(f: Callable[[int32], int32 | str], s: int32) -> int32 | str:
    return f(s)


def call_bytes(f: Callable[[bytearray], int32], b: bytearray) -> int32:
    return f(b)


def call_wopt(f: Callable[[int32], int64 | None], s: int32) -> int64 | None:
    return f(s)


def join(a: str, b: Own[str]) -> int32:
    return len(a) + len(b)


def call64(f: Fn[[int64], int64], s: int64) -> int64:
    return f(s)


def ro_offset(o: readonly[Offset]) -> int64:
    # A readonly object whose one __call__ is @readonly runs through an Fn.
    return call32(o, 5)  # tpyc: ok


def ro_scale(o: readonly[Scale]) -> int64:
    # An undecorated __call__ inferred readonly runs on a readonly object.
    return call32(o, 5)  # tpyc: ok


def ro_scale_c(o: readonly[Scale]) -> int64:
    # The same object at a Callable slot.
    return callc32(o, 5)  # tpyc: ok


def best(xs: list[int32], key: Fn[[int32], int64]) -> int64:
    top: int64 = 0
    for x in xs:
        top = max(top, key(x))
    return top


# Both variants compute the same, as CPython runs the first: the generated
# C++ shows which one a call picks.
@dispatch
def ap(f: Fn[[int32], int64]) -> int64:
    return f(5)


@dispatch
def ap(f: Fn[[int64], int64]) -> int64:
    return f(5)


@dispatch
def choose(f: Callable[[int32], int64], tag: int32) -> int64:
    return f(tag)


@dispatch
def choose(f: Callable[[str], str], tag: str) -> str:
    return f(tag)


class Runner:
    def __init__(self, f: Callable[[int32], int64]) -> None:
        self.f = f

    def run(self, s: int32) -> int64:
        return self.f(s)

    def apply(self, f: Fn[[int32], int64], s: int32) -> int64:
        return f(s)


def pick() -> Callable[[int32], int64]:
    # Return position: a widened function at the declared Callable return.
    return wide  # tpyc: ok


def pass_on(f: Callable[[int64], int64]) -> int64:
    # A Callable parameter passed on at an Fn parameter of a narrower type.
    return call32(f, 4)  # tpyc: ok


def ptr_of(p: P) -> Ptr[P]:
    return p


def call_ptr(f: Callable[[P], Ptr[P]], p: P) -> None:
    f(p).v = 5


class Hp:
    p: P

    def __init__(self) -> None:
        self.p = P(1)


class SubHp(Hp):
    def __init__(self) -> None:
        super().__init__()


def new_p(h: Hp) -> Own[P]:
    return P(h.p.v + 1)


@dispatch
def via(f: Callable[[Hp], P], h: Hp) -> str:
    return "callable " + str(f(h).v)


@dispatch
def via(f: Fn[[SubHp], P], h: Hp) -> str:
    return "fn"


async def double(n: int32) -> int32:
    await asyncio.sleep(0.0)
    return n + n


async def run_fn(f: Fn[[int32], Own[Cancellable[int32]]]) -> int32:
    # Spawned: awaiting the call directly is not lowered yet
    # (BUGS.md#await-callable-coroutine-result-rejects).
    t = asyncio.create_task(f(3))
    return await t


def main() -> None:
    def nested(x: int64) -> int64:
        return x - 1

    # Named function at an Fn argument: int32 widens into the int64 param.
    print("named-fn", call32(wide, 5))  # tpyc: ok
    print("named-callable", callc32(wide, 5))  # tpyc: ok
    print("imported", call32(h_wide, 5))  # tpyc: ok
    print("generic", call32(gen_wide, 5))  # tpyc: ok
    print("nested", call32(nested, 5))  # tpyc: ok
    # Fixed int -> BigInt, int32 -> float, float32 -> float.
    print("bigint", callbig(big, 5))  # tpyc: ok
    print("int-float", callfi(half, 3))  # tpyc: ok
    print("float", callf(half, 3.0))  # tpyc: ok
    # Return widening at a Callable: uint8 into the int64 return. An Fn
    # return is the callable's own, so it takes none.
    print("ret-widen", callc32(ret8, 1))  # tpyc: ok
    # A void slot takes any return.
    callv(shout, 4)  # tpyc: ok
    # Callable value (local and parameter) at an Fn of a narrower param.
    h: Callable[[int64], int64] = wide  # tpyc: ok
    print("callable-local", call32(h, 5))  # tpyc: ok
    print("callable-param", pass_on(h))
    # Callable object whose __call__ takes the wider param.
    print("object", call32(Wide(), 5))  # tpyc: ok
    w = Wide()
    print("object-callable", callc32(w, 5))  # tpyc: ok
    print("object-readonly", ro_offset(Offset(100)))
    # Method and constructor argument positions.
    r = Runner(wide)  # tpyc: ok
    print("ctor", r.run(6))
    print("method", r.apply(wide, 6))  # tpyc: ok
    # Local Callable binding, list element, return position.
    g: Callable[[int32], int64] = wide  # tpyc: ok
    print("local", g(9))
    fs: list[Callable[[int32], int64]] = [wide, h_wide]  # tpyc: ok
    print("list", fs[0](1), fs[1](1))
    print("return", pick()(10))
    # Keyword argument: each int32 element widens into wide's int64 param.
    xs = [3, -1, 2]
    print("key", best(xs, key=wide))  # tpyc: ok
    # A readonly[T] param receives T.
    p = P(1)
    print("readonly", callp(count, p))  # tpyc: ok
    # An X | None param receives X when X is a value type, a str included.
    print("optional", call32(maybe, 4))  # tpyc: ok
    print("optional-str", call_str(maybe_s, "abc"))  # tpyc: ok
    # A subclass C++ inherits: a class base and an inherited @dynamic protocol.
    print("subclass", call_child(getv, Child(10)))  # tpyc: ok
    print("dynamic", call_dog(ask, Dog(12)))  # tpyc: ok
    # No warning: speak is a @dynamic protocol method, so the call through
    # Pet still runs Puppy.speak (30, as in CPython). The second call shows
    # the callback sees the same object, not a copy.
    pup = Puppy(3)
    print("dynamic-member", call_puppy(pet_speak, pup))  # tpyc: ok
    pup.v = 4
    print("dynamic-member-after", call_puppy(pet_speak, pup))  # tpyc: ok
    # A generic callback binds T to the subclass itself: no upcast, so the
    # subclass's override of get runs (a bind to Base would print v, not
    # v * 100), on the object itself, a write made after the first call
    # included.
    kid = Kid(4)
    print("generic-child", call_kid(tag, kid))  # tpyc: ok
    kid.v = 5
    print("generic-child-after", call_kid(tag, kid))  # tpyc: ok
    # The str family: str and StrView share a parameter form; str binds String.
    print("strview", call_str(sv, "hello"))  # tpyc: ok
    print("string", call_string(st, String("abc")))  # tpyc: ok
    # An exact union member, and a tuple widened element by element.
    print("union", call_u(u, 42))  # tpyc: ok
    print("tuple", call_tw(tw, (1, 2)))  # tpyc: ok
    # An overloaded receiver: the exact overload wins over the widened one,
    # for a function and for a Callable value; a named function ranks by
    # its own signature at a Callable parameter.
    print("dispatch", ap(wide), ap(h))  # tpyc: ok
    print("choose", choose(wide, 1))  # tpyc: ok
    # The lambda spelling converts each call as a direct call does.
    print("lambda", call64(lambda x: cb(x), 2000000000))  # tpyc: ok
    # At a Callable, a by-value return converts into a value Optional and
    # into a value union holding it, and widens into an Optional's payload;
    # a widening converts into an Optional parameter's payload.
    print("ret-optional", call_opt(same, 3))  # tpyc: ok
    ru = call_ustr(same, 4)  # tpyc: ok
    print("ret-union", ru)
    print("ret-widen-optional", call_wopt(same, 6))  # tpyc: ok
    print("optional-widen", call32(maybe64, 5))  # tpyc: ok
    # The bytes parameter binds its mutable sibling bytearray.
    print("bytearray", call_bytes(blen, bytearray(b"abc")))  # tpyc: ok
    # An Own[str] callback parameter where map hands over each element.
    print("own-str-map", list(map(join, ["a", "b"], ["cc", "dd"])))  # tpyc: ok
    print("object-readonly-inferred", ro_scale(Scale(3)))
    # A BigInt element is a number: the int32 element converts into it.
    print("tuple-bigint", call_tw(twb, (1, 2)))  # tpyc: ok
    # Own on a number is the number itself, which the slot passes.
    print("own-number", call32(own_inc, 7))  # tpyc: ok
    # Own on any by-value type whose parameter is no view: a BigInt, whose
    # parameter copies the value as a direct call does, and a widened number.
    v: int = 2**70
    rb = callbb(own_big, v)  # tpyc: ok
    print("own-bigint", rb - v)
    print("own-widen", call32(own_wide, 7))  # tpyc: ok
    # A Callable is held by value whatever its signature: it converts into a
    # Callable | None parameter.
    print("optional-callable", callcb(maybe_cb, getp))  # tpyc: ok
    print("object-readonly-inferred-callable", ro_scale_c(Scale(4)))
    # A Ptr return is returned as it is stored, so a Callable keeps the
    # pointer itself: a write through the result reaches the object.
    call_ptr(ptr_of, p)  # tpyc: ok
    print("ptr-return", p.v)
    # A Callable value at an equal Callable parameter of an overloaded
    # receiver is its exact match, ahead of a widened Fn variant; so is a
    # named function of that signature.
    cp: Callable[[Hp], P] = new_p
    print("callable-exact", via(cp, Hp()))  # tpyc: ok
    print("callable-exact-named", via(new_p, Hp()))  # tpyc: ok
    # An async def at an Fn slot taking the owned coroutine handle.
    print("async-fn-own", asyncio.run(run_fn(double)))  # tpyc: ok


main()
