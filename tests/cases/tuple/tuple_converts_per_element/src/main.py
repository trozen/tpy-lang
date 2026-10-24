# A tuple held in another form than its slot converts element by element,
# each element as its scalar converts there (sections name the position).
from typing import Callable, Iterable, Iterator, Self
import asyncio
from tpy import Own, ReturnException, error_return, int32, readonly


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __repr__(self) -> str:
        return f"A({self.v})"


class B:
    w: int32

    def __init__(self, w: int32) -> None:
        self.w = w


class H:
    p: tuple[A, int32]

    def __init__(self, p: Own[tuple[A, int32]]) -> None:
        self.p = p

    def get(self) -> tuple[A, int32]:
        # method: a stored tuple lent element by element
        return self.p  # tpyc: ok


class HO:
    o: tuple[A | None, int32]

    def __init__(self, o: Own[tuple[A | None, int32]]) -> None:
        self.o = o


class Fail(Exception, ReturnException):
    pass


def mka(a: A) -> tuple[Own[A], A]:
    return (A(a.v + 100), a)


class Holder:
    t: tuple[A, A]

    def __init__(self, a: A) -> None:
        # ctor member-init: the call evaluated once in the brace-init, its
        # owned element moved out, the lent one the warned copy
        self.t = mka(a)  # tpyc: warning(/copies A into field \(tuple element 1\)/)


def get_p(h: H) -> tuple[A, int32]:
    # free function: tuple of references lent out of a stored tuple
    return h.p  # tpyc: ok


def each_p(h: H) -> Iterator[tuple[A, int32]]:
    # generator: the same lend from the frame's parameter
    yield h.p  # tpyc: ok


def get_finally(h: H) -> tuple[A, int32]:
    try:
        # try/finally: a staged result, lent the same way
        return h.p  # tpyc: ok
    finally:
        print("finally ran")


@error_return(Fail)
def er_copy(t: tuple[A, int32]) -> tuple[Own[A], int32]:
    # @error_return body: a parameter at an owning element, the warned copy
    # (built, never called: BUGS.md#tuple-result-error-return-bind)
    return t  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


async def aget(h: H) -> int32:
    r = get_p(h)
    await asyncio.sleep(0)
    # async body: the lend survives the suspension
    return r[0].v


def nest_ret(t: tuple[tuple[A, int32], int32]) -> tuple[tuple[Own[A], int32], int32]:
    # nested: the inner tuple converts element by element too, the warned
    # copy (built, never called: BUGS.md#nested-borrow-tuple-arg-rejects)
    return t  # tpyc: warning(/copies A into owned storage \(tuple element 0.0\)/)


def opt_get(h: HO) -> tuple[A | None, int32]:
    # Optional leaf: the stored optional lent as its pointer (its union twin
    # does not lower: BUGS.md#union-leaf-tuple-lend-unlowered)
    return h.o  # tpyc: ok


def opt_ret(t: tuple[A | None, int32]) -> tuple[Own[A | None], int32]:
    # Optional leaf of a parameter at an owning element: the warned copy
    # (built, never called: BUGS.md#own-optional-leaf-tuple-result-unconsumed)
    return t  # tpyc: warning(/copies A \| None into owned storage \(tuple element 0\)/)


def uni_ret(t: tuple[A | B, int32]) -> tuple[Own[A | B], int32]:
    # union leaf of a parameter at an owning element: the warned copy (built,
    # never called: BUGS.md#union-leaf-tuple-lend-unlowered)
    return t  # tpyc: warning(/copies A \| B into owned storage \(tuple element 0\)/)


def first[T](t: tuple[T, int32]) -> tuple[Own[T], int32]:
    # open T at an owning element: the hedged copy
    return t  # tpyc: warning(/may copy T into owned storage \(tuple element 0\)/)


def first_str() -> tuple[str, int32]:
    # open T instantiated at str: the element is a view of the literal
    return first(("s", 7))


def own_own[T](t: tuple[T, int32]) -> Own[tuple[Own[T], int32]]:
    # open T at a wholly owned result: the hedged copy
    return t  # tpyc: warning(/may copy T into owned storage \(tuple element 0\)/)


class HN:
    q: tuple[tuple[A, int32], int32]

    def __init__(self, q: tuple[tuple[A, int32], int32]) -> None:
        # ctor member-init of a nested parameter, element by element (built,
        # never called: BUGS.md#nested-borrow-tuple-arg-rejects)
        self.q = q  # tpyc: warning(/copies A into field \(tuple element 0.0\)/)


def first_a(t: tuple[A, int32]) -> tuple[Own[A], int32]:
    # its monomorphic twin
    return t  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


def cget(h: readonly[H]) -> tuple[readonly[A], int32]:
    # const source: lent as const pointers
    return h.p  # tpyc: ok


def pair(a: A) -> tuple[A, int32]:
    return (a, 2)


def mkb(a: A) -> tuple[A, int32]:
    print("mkb", a.v)
    return (a, 1)


def take(t: Own[tuple[A, int32]]) -> bool:
    print("take", t[0].v, t[1])
    return True


def keep(xs: list[tuple[A, A]], a: A) -> None:
    # Own container element: the call evaluated once, its owned element
    # moved out, the lent one the warned copy
    xs.append(mka(a))  # tpyc: warning(/copies A into owned storage \(tuple element 1\)/)


def values(t: Own[tuple[A, A]]) -> Own[list[int32]]:
    return [t[0].v, t[1].v]


def lend_list(t: Own[tuple[A, A]], xs: list[int32]) -> list[int32]:
    return xs


def frame_for(a: A) -> Iterator[int32]:
    # a generator's for-source: the frame spells its field type from the
    # converted call under a class-scope `decltype`, where the lambda binding
    # the call is captureless
    for n in values(mka(a)):  # tpyc: warning(/copies A into owned storage \(tuple element 1\)/)
        yield n


async def first_of(it: Iterable[int32]) -> int32:
    for n in it:
        return n
    return 0


async def await_arg(a: A, xs: list[int32]) -> int32:
    # an await's argument: the sub-coroutine's field type likewise
    return await first_of(lend_list(mka(a), xs))  # tpyc: warning(/copies A into owned storage \(tuple element 1\)/)


def mut_first(t: tuple[A, int32]) -> int32:
    t[0].v += 1
    return t[0].v


def idx() -> int32:
    return 0


def held_yield(xs: list[tuple[A, int32]]) -> Iterator[tuple[A, int32]]:
    # generator yield: an element by a computed index held once in the
    # step, lent element by element
    yield xs[idx()]  # tpyc: ok


def call_yield(a: A) -> Iterator[tuple[Own[A], int32]]:
    # generator yield of a call converted element by element: held once,
    # its lent element the warned copy (a copy by design, so the caller
    # does not mutate it)
    yield pair(a)  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


async def held_return(d: dict[str, tuple[A, int32]], k: str) -> int32:
    await asyncio.sleep(0)
    try:
        # async return: a keyed lookup held once for the call, ahead of the
        # return's capture before the finally
        return mut_first(d[k])  # tpyc: ok
    finally:
        print("async.keyed finally")


def firsts(t: tuple[A, int32]) -> Own[list[int32]]:
    t[0].v += 1
    return [t[0].v]


def held_for(xs: list[tuple[A, int32]]) -> Iterator[int32]:
    # a generator's for-source: the held element spelled under `decltype`
    for n in firsts(xs[idx()]):  # tpyc: ok
        yield n


def arg_field(h: H) -> int32:
    # call argument: a stored tuple lent element by element for the call
    return mut_first(h.p)  # tpyc: ok


def walrus_lend(xs: list[tuple[A, int32]]) -> int32:
    # walrus: a stored element lent element by element to the target
    if (w := xs[0])[1] > 0:  # tpyc: ok
        w[0].v += 5
    return xs[0][0].v


def loop_hoisted() -> int32:
    xs: list[tuple[A, int32]] = [(A(1), 1), (A(2), 2)]
    for t in xs:
        pass
    # free-function for-loop step: the loop var used after the loop takes
    # each step lent element by element, so it aliases the last element
    t[0].v += 3
    return xs[1][0].v


def gen_step(d: dict[int32, A]) -> Iterator[int32]:
    for kv in d.items():
        # generator for-loop step: the dict_items proxy lent per element
        kv[1].v += 1  # tpyc: ok
        yield kv[0]


async def async_step(d: dict[int32, A]) -> int32:
    n = 0
    for kv in d.items():
        await asyncio.sleep(0)
        # async for-loop step: the proxy lent per element, held across the
        # suspension
        kv[1].v += 10  # tpyc: ok
        n += kv[0]
    return n


async def seated(t: tuple[A, int32]) -> int32:
    await asyncio.sleep(0)
    t[0].v += 1
    return t[0].v + t[1]


async def frame_seat(a: A) -> int32:
    # frame-seated argument across an await: the temporary element lives in
    # a statement temporary the frame keeps, the lvalue one stays lent
    x = await seated((A(50), 1))  # tpyc: ok
    y = await seated((a, 2))  # tpyc: ok
    return x + y


class HOP:
    o: tuple[A | None, int32]

    def __init__(self, o: tuple[A | None, int32]) -> None:
        # ctor member-init of an Optional leaf parameter, element by element
        self.o = o  # tpyc: warning(/copies A \| None into field \(tuple element 0\)/)


class C2:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __copy__(self) -> Own[Self]:
        print("arg.mixed copy", self.n)
        return C2(self.n)


def take_mixed(p: tuple[Own[C2], B]) -> Own[C2]:
    p[1].w += 1
    return p[0]


# Each hash call of a K; the map's own construction may hash more than once.
hashes: list[int32] = []


class K:
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k

    def __hash__(self) -> int:
        hashes.append(self.k)
        return self.k

    def __eq__(self, o: "K") -> bool:
        return o.k == self.k


def keyed(k: K) -> None:
    d = {k: (3, A(4), A(5))}  # tpyc: warning(/copies K into owned storage/)
    before = len(hashes)
    # a lookup by a name key is held once, not repeated per element
    a, b, c = d[k]  # tpyc: ok
    print("keyed hashes", len(hashes) - before)
    b.v += 10
    back = d[k][1].v
    print("keyed", a, b.v, c.v, back)


def mkn(a: A) -> tuple[Own[B], A]:
    return (B(1), a)


def sinkn(t: tuple[Own[B], Own[A]]) -> int32:  # tpyc: warning(/never consumed/)
    return t[0].w + t[1].v


def mixed_last(a: A) -> int32:
    t = mkn(a)
    # a mixed local at its last use into an Own-element parameter: the owned
    # element moves, the borrowed one is the warned copy
    return sinkn(t)  # tpyc: warning(/copies A into owned storage \(tuple element 1\)/)


def mixed_ins(a: A, xs: list[tuple[B, A]]) -> None:
    t = mkn(a)
    # ... and into a container element
    xs.append(t)  # tpyc: warning(/copies A into owned storage \(tuple element 1\)/)


class Loud:
    v: int32

    def __init__(self, v: int32 = 0) -> None:
        print("loud init", v)
        self.v = v


class Mortal:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __del__(self) -> None:
        print("arg.life del", self.v)


def first_n() -> int32:
    print("arg.order first")
    return 1


def read_loud(t: tuple[int32, Loud]) -> int32:
    return t[0] + t[1].v


def read_mortal(t: tuple[Mortal, int32]) -> int32:
    return t[0].v + t[1]


def read_a(t: tuple[int32, A]) -> int32:
    return t[0] + t[1].v


def read_opt(t: tuple[int32, A | None]) -> int32:
    return t[0]


def read_uni(t: tuple[int32, A | B]) -> int32:
    return t[0]


def mk_loud(p: tuple[Loud, int32]) -> tuple[Own[Loud], int32]:
    return (Loud(p[0].v + p[1]), p[1])


class HL:
    p: tuple[Loud, int32]

    def __init__(self) -> None:
        # ctor member-init: the const element lends its temporary in place,
        # so the member-init stays (no default construction first)
        self.p = mk_loud((Loud(2), 3))  # tpyc: ok


def held_ix(tag: str) -> int32:
    print(tag, "index")
    return 0


def held_grow(xs: list[tuple[int32, A]]) -> int32:
    for i in range(100):
        xs.insert(0, (10, A(20)))
    return 0


def held_order(xs: list[tuple[int32, A]]) -> tuple[int32, int32]:
    # a source read by reference is evaluated where it stands: after the
    # operand to its left grew the list, never ahead of the statement
    return (held_grow(xs), read_a(xs[held_ix("held.order")]))  # tpyc: ok


def held_cond(xs: list[tuple[int32, A]], flag: bool) -> int32:
    # ... and only on the arm taken
    return read_a(xs[held_ix("held.cond")]) if flag else 9  # tpyc: ok


def held_key(d: dict[str, tuple[int32, A]], k: str) -> int32:
    # a keyed lookup by a key built in place: the key lives to the end of
    # the full expression too
    return mut_t(d[k + "x"])  # tpyc: ok


def mut_t(t: tuple[int32, A]) -> int32:
    t[1].v += 1
    return t[0] + t[1].v


def loud_of(t: tuple[int32, A]) -> Own[Loud]:
    t[1].v += 1
    return Loud(t[1].v)


def loud_mixed(t: Own[tuple[Loud, Loud]]) -> Own[Loud]:
    return Loud(t[0].v + t[1].v)


def mixed_loud(b: Loud) -> tuple[Own[Loud], Loud]:
    return (Loud(b.v + 1), b)


class HI:
    r: Loud

    def __init__(self, xs: list[tuple[int32, A]], i: int32) -> None:
        # ctor member-init: a source read by reference binds in the
        # brace-init, so the member is never default-constructed first
        self.r = loud_of(xs[i])  # tpyc: ok


class HJ:
    r: Loud

    def __init__(self, xs: list[tuple[int32, A]]) -> None:
        self.r = loud_of(xs[held_ix("held.ctor")])  # tpyc: ok


class HV:
    r: Loud

    def __init__(self, b: Loud) -> None:
        # ... and a call held by value inside a member-init argument
        self.r = loud_mixed(mixed_loud(b))  # tpyc: warning(/copies Loud into owned storage \(tuple element 1\)/)


def held_bounds(xs: list[tuple[int32, A]], j: int32) -> int32:
    n = 0
    for i in range(len(xs)):
        # an element read proven in range is a place, read per element
        n += read_a(xs[i])  # tpyc: bounds_safe(xs)
    # ... one that is not is held once, so its bounds check runs once
    return n + read_a(xs[j])  # tpyc: bounds_checked(xs)


def match_lent_type(t: tuple[A | B, int32]) -> str:
    # a union element a borrow-form tuple lends is its pointer variant: a
    # type-only match reads it as the borrowed union parameter's (a capture
    # is refused: generators/error_lent_union_element_match)
    match t[0]:  # tpyc: ok
        case A():
            return "A"
        case _:
            return "other"


def mk_ab() -> Own[tuple[A, A | B]]:
    return (A(1), B(2))


def lend_union() -> Iterator[tuple[Own[A], A | B]]:
    t = mk_ab()
    yield t


def make_s(n: int32) -> str:
    return "ab" if n > 0 else ""


def count_to(n: int32) -> Iterator[int32]:
    yield n


def frame_select(n: int32) -> Iterator[int32]:
    # a deduced temp under a frame's class-scope `decltype` (the value
    # select's left operand) is spelled from its initializer
    for c in count_to(len(make_s(n) or "xyz")):  # tpyc: ok
        yield c


def pair_s(n: int32) -> tuple[str, int32]:
    return (make_s(n) * n, n)


def first_s(t: tuple[str, int32]) -> tuple[Own[str], int32]:
    return t


def generic_dyn(n: int32) -> tuple[str, int32]:
    # open T at str from a string built at run time
    return first((make_s(n) * 3, 7))  # tpyc: ok


def twin_dyn(n: int32) -> tuple[str, int32]:
    # ... and its monomorphic twin
    return first_s((make_s(n) * 3, 7))  # tpyc: ok


def generic_call(n: int32) -> tuple[str, int32]:
    # open T at str from a call source
    return first(pair_s(n))  # tpyc: ok


def twin_call(n: int32) -> tuple[str, int32]:
    return first_s(pair_s(n))  # tpyc: ok


def make_whole() -> Own[tuple[Own[A], A]]:
    return (A(1), A(2))


def read_whole(t: tuple[A, A]) -> int32:
    t[1].v += 10
    return t[0].v + t[1].v


def whole_owned() -> None:
    t = make_whole()
    # a wholly owned tuple holds its plain element inline too (not the mixed
    # result form): lent element by element to a borrow-form parameter
    n = read_whole(t)  # tpyc: ok
    print("whole.lend", n, t[1].v)
    xs: list[tuple[A, A]] = []
    # ... and passed whole into a container element
    xs.append(make_whole())  # tpyc: ok
    print("whole.append", xs[0][0].v, xs[0][1].v)


def read_nest(t: tuple[tuple[A, int32], A]) -> int32:
    return t[1].v


class NestCtor:
    n: int32

    def __init__(self, a: A) -> None:
        # ctor member-init: a nested literal beside an rvalue element
        self.n = read_nest(((a, 2), A(3)))  # tpyc: ok


def nest_gen(a: A) -> Iterator[int32]:
    # generator yield: the same, in a frame
    yield read_nest(((a, 2), A(4)))  # tpyc: ok


async def nest_async(a: A) -> int32:
    await asyncio.sleep(0)
    # async return: the same, in a coroutine frame
    return read_nest(((a, 2), A(5)))  # tpyc: ok


def nest_literal() -> None:
    a = A(1)
    # free function: a nested literal of lvalues keeps its const pointees
    # while its rvalue sibling is lent from the lambda's parameter
    print("nest.free", read_nest(((a, 2), A(3))))  # tpyc: ok
    print("nest.ctor", NestCtor(a).n)
    for n in nest_gen(a):
        print("nest.gen", n)
    print("nest.async", asyncio.run(nest_async(a)))
    # comprehension element
    print("nest.comp", [read_nest(((a, 2), A(i))) for i in range(1, 3)])  # tpyc: ok
    # lambda body
    fn: Callable[[A], int32] = lambda x: read_nest(((x, 2), A(6)))  # tpyc: ok
    print("nest.lambda", fn(a))


def read_pair(t: tuple[A, A]) -> int32:
    t[1].v += 1
    return t[0].v + t[1].v


def read_pair_ro(t: tuple[A, A]) -> int32:
    return t[0].v + t[1].v


def stored_mixed(xs: list[tuple[Own[A], A]]) -> None:
    # A container's mixed tuple is its stored tuple, both elements inline:
    # lent element by element, in a comprehension and a `for` alike. No
    # program builds such a list yet, so this body is only compiled.
    print("stored.comp", [read_pair_ro(t) for t in xs])  # tpyc: ok
    for t in xs:
        print("stored.for", read_pair_ro(t))  # tpyc: ok


def mixed_gen(b: A) -> Iterator[tuple[Own[A], A]]:
    yield (A(1), b)
    yield (A(2), b)


def yielded_mixed() -> None:
    b = A(10)
    # a generator's mixed step lends its plain element: the write reaches b
    print("yielded.comp", [read_pair(t) for t in mixed_gen(b)], b.v)  # tpyc: ok
    for t in mixed_gen(b):
        print("yielded.for", read_pair(t), b.v)  # tpyc: ok


class HT:
    t: tuple[A | None, int32]

    def __init__(self, n: int32) -> None:
        self.t = (A(n), 1)


def make_ht(n: int32) -> Own[HT]:
    return HT(n)


def read_opt_first(t: tuple[A | None, int32]) -> int32:
    a, n = t
    if a is not None:
        return a.v + n
    return n


def temp_optional() -> None:
    # an Optional element of a temporary is lent for the call's full
    # expression, from the tuple's lambda parameter
    print("temp.optional", read_opt_first(make_ht(7).t))  # tpyc: ok


def main() -> None:
    h = H((A(1), 2))
    r = get_p(h)
    h.p[0].v = 11
    print("free", r[0].v, r[1])
    m = h.get()
    h.p[0].v = 12
    print("method", m[0].v, m[1])
    for g in each_p(h):
        h.p[0].v = 13
        print("generator", g[0].v, g[1])
    f = get_finally(h)
    h.p[0].v = 15
    print("finally", f[0].v, f[1])
    print("async", asyncio.run(aget(h)))
    # lambda result: the call evaluated once where it stands, its lent
    # element the warned copy into the owning result
    lam: Callable[[A], tuple[Own[A], int32]] = lambda x: pair(x)  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)
    lr = lam(A(16))
    print("lambda", lr[0].v, lr[1])
    ho = HO((A(1), 2))
    # no read of the lent Optional element lowers yet, so the alias is not
    # observed here (BUGS.md#lent-optional-tuple-element-match-ice)
    orr = opt_get(ho)
    print("optional", orr[1])
    g1 = first((A(5), 6))
    print("generic.class", g1[0].v, g1[1])
    g2 = first_str()
    print("generic.str", g2[0], g2[1])
    g3 = first_a((A(8), 9))
    print("generic.twin", g3[0].v, g3[1])
    g4 = own_own((A(10), 11))
    print("generic.own", g4[0].v, g4[1])
    cr = cget(h)
    h.p[0].v = 61
    print("const", cr[0].v, cr[1])
    hold = Holder(A(70))
    print("ctor", hold.t[0].v, hold.t[1].v)
    xs: list[tuple[A, A]] = []
    keep(xs, A(80))
    print("container", xs[0][0].v, xs[0][1].v)
    a = A(90)
    c = True
    # conditional operand: the call runs only where the guard lets it
    r1 = c or take(mkb(a))  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)
    print("or", r1)
    r2 = c and take(mkb(a))  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)
    print("and", r2)
    for fv in frame_for(A(91)):
        print("frame_for", fv)
    print("await_arg", asyncio.run(await_arg(A(92), [93])))
    hx = [(A(40), 1)]
    for hy in held_yield(hx):
        hy[0].v += 1
    print("held.yield", hx[0][0].v)
    ca = A(41)
    for cy in call_yield(ca):
        print("call.yield", cy[0].v, ca.v)
    hd = {"k": (A(42), 1)}
    hr = asyncio.run(held_return(hd, "k"))
    print("async.keyed", hr, hd["k"][0].v)
    for hn in held_for(hx):
        print("held.for", hn, hx[0][0].v)
    print("arg.field", arg_field(h), h.p[0].v)
    # call argument: a temporary element lent for the call
    print("arg.temp", mut_first((A(30), 1)))
    wl = [(A(1), 1)]
    print("walrus", walrus_lend(wl))
    print("loop.free", loop_hoisted())
    d = {1: A(1), 2: A(2)}
    for k in gen_step(d):
        print("loop.gen", k)
    print("loop.gen", d[1].v, d[2].v)
    print("loop.async", asyncio.run(async_step(d)), d[1].v, d[2].v)
    fa = A(60)
    print("frame_seat", asyncio.run(frame_seat(fa)), fa.v)
    pa = A(7)
    # print: a tuple literal of lvalues lent to the printer
    print("print", (pa, 1))
    hop = HOP((A(6), 7))
    print("ctor.optional", hop.o[1])
    mb = B(2)
    # call argument at a mixed parameter: the fresh owned element is built
    # in place (no copy), the lvalue one lent
    mc = take_mixed((C2(1), mb))  # tpyc: ok
    print("arg.mixed", mc.n, mb.w)
    # both elements fresh: the borrowed one lent for the call
    print("arg.mixed", take_mixed((C2(3), B(4))).n)  # tpyc: ok
    # a temporary at a const element is lent for the call's full
    # expression: it runs after the operands to its left
    n1 = read_loud((first_n(), Loud(2)))  # tpyc: ok
    print("arg.order", n1)
    off = False
    # ... and only where the guard lets it
    n2 = off or read_loud((first_n(), Loud(3))) > 0  # tpyc: ok
    print("arg.or", n2)
    # ... and dies with its statement
    n3 = read_mortal((Mortal(1), 2))  # tpyc: ok
    print("arg.life", n3)
    # ... with no statement to precede, in a lambda body
    lam_c: Callable[[int32], int32] = lambda x: read_a((x, A(2)))  # tpyc: ok
    print("arg.lambda", lam_c(3))
    hl = HL()
    print("arg.ctor", hl.p[0].v, hl.p[1])
    # the Optional and union leaves lend through their scalar lend
    print("arg.optional", read_opt((1, A(3))))  # tpyc: ok
    print("arg.union", read_uni((1, A(4))))  # tpyc: ok
    keyed(K(1))
    print("own_param.mixed", mixed_last(A(10)))
    mx: list[tuple[B, A]] = []
    mixed_ins(A(20), mx)
    print("own_elem.mixed", mx[0][0].w, mx[0][1].v)
    ro: list[tuple[A, readonly[A]]] = []
    a30 = A(30)
    # a mixed call into a container whose tuple declares a readonly element
    ro.append(mka(a30))  # tpyc: warning(/copies readonly\[A\] into owned storage/)
    print("readonly_elem.mixed", ro[0][0].v, ro[0][1].v)
    ys = [(1, A(2))]
    ho1 = held_order(ys)
    print("held.order", ho1[0], ho1[1], len(ys))
    hc1 = held_cond(ys, False)
    hc2 = held_cond(ys, True)
    print("held.cond", hc1, hc2)
    hk = {"ax": (1, A(2))}
    hkn = held_key(hk, "a")
    print("held.key", hkn, hk["ax"][1].v)
    zs = [(1, A(5))]
    hi = HI(zs, 0)
    hj = HJ(zs)
    print("held.ctor", hi.r.v, hj.r.v, zs[0][1].v)
    hv = HV(Loud(3))
    print("held.ctor.value", hv.r.v)
    print("held.bounds", held_bounds([(1, A(2)), (3, A(4))], 1))
    print("match.lent", match_lent_type((A(5), 1)),
          match_lent_type((B(7), 1)))
    for fs in frame_select(1):
        print("frame.select", fs)
    for fs in frame_select(0):
        print("frame.select", fs)
    for q in lend_union():
        # a mixed step lends its union element as the pointer variant
        match q[1]:  # tpyc: ok
            case B():
                print("match.lent_gen", q[0].v, "B")
            case _:
                print("match.lent_gen other")
    gd = generic_dyn(2)
    td = twin_dyn(2)
    print("generic.dyn", gd[0] == td[0], len(gd[0]), gd[1])
    gc = generic_call(2)
    tc = twin_call(2)
    print("generic.call", gc[0] == tc[0], gc[0], gc[1])
    whole_owned()
    nest_literal()
    yielded_mixed()
    temp_optional()


main()
