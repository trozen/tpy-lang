# An escaping (Callable) closure copies each capture when it is created; the
# warnings name every divergence from CPython's late-binding closure, one
# section per position. Each section mutates or rebinds after creation.
import asyncio
from typing import Callable, Iterator, Protocol
from tpy import int32, Own, ReturnException, auto_readonly, dynamic, error_return


class P:
    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    fs: list[Callable[[], int32]]

    def __init__(self) -> None:
        self.fs = []

    def keep(self, f: Callable[[], int32]) -> None:
        self.fs.append(f)


def call_now(f: Callable[[], int32]) -> int32:
    return f()


# local: a list local the lambda copies
def local() -> None:
    xs = [1, 2]
    read: Callable[[], int32] = lambda: len(xs)  # tpyc: warning(/lambda at line 31 copies the object 'xs' refers to/)
    xs.append(3)
    print("local:", read())


# alias / elem: an alias of a local and a borrowed container element
def alias_elem(ps: list[P]) -> None:
    xs = [1, 2]
    ys = xs
    p = ps[0]
    read: Callable[[], int32] = lambda: len(ys) + p.n  # tpyc: warning(/copies the object 'ys'/) warning(/copies the object 'p'/)
    xs.append(3)
    ps[0].n = 5
    print("alias_elem:", read())


# param: a borrowed reference parameter
def param(xs: list[int32]) -> Callable[[], int32]:
    return lambda: len(xs)  # tpyc: warning(/copies the object 'xs'/)


# own_param: an Own[T] parameter the lambda copies rather than moves
def own_param(xs: Own[list[int32]]) -> Callable[[], int32]:  # tpyc: warning(/never consumed/)
    f: Callable[[], int32] = lambda: len(xs)  # tpyc: warning(/copies the object 'xs'/)
    xs.append(3)
    return f


# method: a method local (the receiver itself is captured as itself)
class M:
    k: int32

    def __init__(self) -> None:
        self.k = 10

    def make(self) -> Callable[[], int32]:
        xs = [1]
        f: Callable[[], int32] = lambda: len(xs) + self.k  # tpyc: warning(/copies the object 'xs'/)
        xs.append(2)
        return f


# ctor_field: a lambda stored into a field in __init__ over a parameter
class C:
    getter: Callable[[], int32]

    def __init__(self, p: P) -> None:
        self.getter = lambda: p.n  # tpyc: warning(/copies the object 'p'/)


# ret / store / arg: returned, appended to a list, stored by a callee
def ret_store_arg(h: Holder) -> Callable[[], int32]:
    xs = [1]
    fs: list[Callable[[], int32]] = []
    fs.append(lambda: len(xs) * 10)  # tpyc: warning(/copies the object 'xs'/)
    h.keep(lambda: len(xs) * 100)  # tpyc: warning(/copies the object 'xs'/)
    out: Callable[[], int32] = lambda: len(xs) * 1000  # tpyc: warning(/copies the object 'xs'/)
    xs.append(2)
    print("store:", fs[0]())
    return out


# comp: lambdas built by a comprehension, over its variable and a local
def comp() -> None:
    xs = [1]
    fs: list[Callable[[], int32]] = [lambda: len(xs) + i for i in range(2)]  # tpyc: warning(/copies the object 'xs'/) warning(/'i' is bound again on each iteration/)
    xs.append(2)
    print("comp:", fs[0](), fs[1]())


# curried: an inner lambda over the enclosing lambda's parameter
def curried() -> Callable[[list[int32]], Callable[[], int32]]:
    return lambda zs: (lambda: len(zs))  # tpyc: warning(/lambda at line 103 copies the object 'zs'/)


# block / loop: a loop-body local, the loop target and an outer value local
def block(ps: list[P]) -> None:
    fs: list[Callable[[], int32]] = []
    k = 0
    for q in ps:
        snap = q
        k += 1
        fs.append(lambda: snap.n + k * 100)  # tpyc: warning(/copies the object 'snap'/) warning(/'snap' is bound again/) warning(/'k' is bound again/)
        fs.append(lambda: q.n)  # tpyc: warning(/copies the object 'q'/) warning(/'q' is bound again/)
    ps[0].n = 9
    print("block:", fs[0](), fs[1](), fs[2](), fs[3]())


# block_def: the same loop-body local captured by an escaping nested def
def block_def(ps: list[P]) -> None:
    fs: list[Callable[[], int32]] = []
    for q in ps:
        snap = q

        def get() -> int32:  # tpyc: warning(/nested function 'get' copies the object 'snap'/) warning(/'snap' is bound again on each iteration; the nested function 'get'/)
            return snap.n
        fs.append(get)
    ps[0].n = 8
    print("block_def:", fs[0](), fs[1]())


# tuple_def: a tuple of records a nested def does not move (a tuple local may
# be the borrow form, so the wording hedges)
def tuple_def() -> Callable[[], int32]:
    t = (P(1), P(2))

    def tget() -> int32:  # tpyc: warning(/nested function 'tget' may keep its own copy of 't'/)
        return t[0].n
    t[0].n = 7
    print("tuple_def:", tget(), t[0].n)
    return tget


# tuple_elem: a tuple local -- an element read off a list, a literal -- may be
# the borrow form, so its wording hedges; an iteration step over the list
# holds the element itself
def tuple_elem(ts: list[tuple[P, P]]) -> None:
    t = ts[0]
    u = (P(3), P(4))
    f: Callable[[], int32] = lambda: t[0].n + u[1].n  # tpyc: warning(/may keep its own copy of 't'/) warning(/may keep its own copy of 'u'/)
    gs: list[Callable[[], int32]] = []
    for v in ts:
        gs.append(lambda: v[1].n)  # tpyc: warning(/copies the object 'v'/) warning(/'v' is bound again/)
    ts[0][0].n = 20
    ts[0][1].n = 30
    u[1].n = 40
    print("tuple_elem:", f(), gs[0]())


# mixed: a tuple of a new record and a borrowed one; only the new one is copied
def mixed() -> None:
    p = P(1)
    t = (P(2), p)
    f: Callable[[], int32] = lambda: t[0].n + t[1].n  # tpyc: warning(/may keep its own copy of 't'/)
    p.n = 10
    t[0].n = 20
    print("mixed:", f())


# gen: a generator's parameter, a frame member the lambda copies
def gen(xs: list[int32]) -> Iterator[int32]:
    f: Callable[[], int32] = lambda: len(xs)  # tpyc: warning(/copies the object 'xs'/)
    yield f()
    xs.append(9)
    yield f()


# nested: a nested def's own lambda over the def's local
def nested() -> int32:
    def inner() -> Callable[[], int32]:
        ys = [1]
        r: Callable[[], int32] = lambda: len(ys)  # tpyc: warning(/copies the object 'ys'/)
        ys.append(2)
        return r
    return inner()()


# compound: inside with / try / match bodies
class Ctx:
    def __enter__(self) -> int32:
        return 1

    def __exit__(self, et, ev, tb) -> None:
        pass


def compound(code: int32) -> None:
    xs = [1]
    fs: list[Callable[[], int32]] = []
    with Ctx() as _c:
        fs.append(lambda: len(xs))  # tpyc: warning(/copies the object 'xs'/)
    try:
        fs.append(lambda: len(xs) * 10)  # tpyc: warning(/copies the object 'xs'/)
    finally:
        pass
    match code:
        case 1:
            fs.append(lambda: len(xs) * 100)  # tpyc: warning(/copies the object 'xs'/)
        case _:
            pass
    xs.append(2)
    print("compound:", fs[0](), fs[1](), fs[2]())


# async: a coroutine's parameter, a frame member the lambda copies
async def co_body(xs: list[int32]) -> int32:
    f: Callable[[], int32] = lambda: len(xs)  # tpyc: warning(/copies the object 'xs'/)
    xs.append(2)
    await asyncio.sleep(0)
    return f()


# er: a local in an @error_return body is copied; a binding of an
# @error_return call's result may refer to the callee's storage, so it hedges
class Fail(Exception, ReturnException):
    pass


@error_return(Fail)
def er_make(n: int32) -> Own[P]:
    if n < 0:
        raise Fail
    return P(n)


@error_return(Fail)
def er_body(n: int32) -> int32:
    xs = [n]
    f: Callable[[], int32] = lambda: len(xs)  # tpyc: warning(/copies the object 'xs'/)
    q = er_make(n)
    g: Callable[[], int32] = lambda: q.n  # tpyc: warning(/may keep its own copy of 'q'/)
    xs.append(2)
    q.n = 3
    return f() * 10 + g()


# auto_readonly: both bodies of the pair build the lambda; it warns once
class Bag:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1]

    @auto_readonly
    def reader(self) -> Callable[[], int32]:
        xs = [len(self.items)]
        f: Callable[[], int32] = lambda: len(xs)  # tpyc: warning(/copies the object 'xs'/)
        xs.append(2)
        return f


# reseat / rebind_after: an outer reference local the loop points at another
# object, before or after the lambda: each lambda keeps its own pointer
def reseat(ps: list[P]) -> None:
    fs: list[Callable[[], int32]] = []
    p = ps[0]
    for i in range(len(ps)):
        p = ps[i]
        fs.append(lambda: p.n)  # tpyc: warning(/may keep its own copy of 'p'/) warning(/'p' may be bound again/)
    print("reseat:", fs[0](), fs[1](), fs[2]())


def rebind_after() -> None:
    fs: list[Callable[[], int32]] = []
    p = P(0)
    for i in range(3):
        fs.append(lambda: p.n)  # tpyc: warning(/may keep its own copy of 'p'/) warning(/'p' may be bound again/)
        if i == 1:
            p = P(i + 10)
    print("rebind_after:", fs[0](), fs[1](), fs[2]())


# while_unpack: a while loop's local and a tuple-unpacking loop head
def while_unpack(pairs: list[tuple[int32, int32]]) -> None:
    fs: list[Callable[[], int32]] = []
    i = 0
    while i < 2:
        k = i * 10
        fs.append(lambda: k)  # tpyc: warning(/'k' is bound again/)
        i += 1
    for a, b in pairs:
        fs.append(lambda: a + b)  # tpyc: warning(/'a' is bound again/) warning(/'b' is bound again/)
    print("while_unpack:", fs[0](), fs[1](), fs[2](), fs[3]())


# generic: a type holding an open T is not settled here, so the wording hedges
def keep_t[T](xs: list[T]) -> Callable[[], int32]:
    return lambda: len(xs)  # tpyc: warning(/may keep its own copy of 'xs'/)


# cost: an expensive immutable value (an owned str, an int) is copied
def cost(k: int) -> Callable[[], int]:
    s = "v" + str(k)
    return lambda: k + len(s)  # tpyc: warning(/copies 'k' when it is created/) warning(/copies 's' when it is created/)


# rebound_param: an Own parameter the body may rebind; the wording hedges
def rebound_param(p: Own[P], c: bool) -> None:  # tpyc: warning(/never consumed/)
    if c:
        p = P(5)
    f: Callable[[], int32] = lambda: p.n  # tpyc: warning(/may keep its own copy of 'p'/)
    p.n = 9
    print("rebound_param:", f())


# dynamic: a @dynamic protocol local is not settled here, so the wording
# hedges (the local itself copies the record, BUGS.md#dyn-local-lvalue-bind-copies)
@dynamic
class Shape(Protocol):
    def area(self) -> int32: ...


class Sq:
    def __init__(self, k: int32) -> None:
        self.k = k

    def area(self) -> int32:
        return self.k * self.k


def dyn_local(sq: Sq) -> None:
    s: Shape = sq
    f: Callable[[], int32] = lambda: s.area()  # tpyc: warning(/may keep its own copy of 's'/)
    sq.k = 3
    print("dynamic:", f())


# callable_state: a captured Callable copies the callable object it holds,
# so the calls through the original do not advance the lambda's copy
class Counter:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __call__(self, xs: list[int32]) -> int32:
        self.n += len(xs)
        return self.n


def callable_state() -> None:
    f: Callable[[list[int32]], int32] = Counter()
    g: Callable[[list[int32]], int32] = lambda xs: f(xs)  # tpyc: warning(/may keep its own copy of 'f'/)
    print("callable_state:", f([1]), g([1]), f([1]), g([1]))


def main() -> None:
    local()
    ps = [P(1), P(2)]
    alias_elem(ps)
    xs = [1]
    f = param(xs)
    xs.append(2)
    print("param:", f())
    ys = [1, 2]
    print("own_param:", own_param(ys)())
    m = M()
    g = m.make()
    m.k = 20
    print("method:", g())
    p = P(1)
    c = C(p)
    p.n = 5
    print("ctor_field:", c.getter())
    h = Holder()
    r = ret_store_arg(h)
    print("arg:", h.fs[0](), "ret:", r())
    comp()
    zs = [1]
    inner = curried()(zs)
    zs.append(2)
    print("curried:", inner())
    block([P(1), P(2)])
    block_def([P(1), P(2)])
    tget = tuple_def()
    print("tuple_def:", tget())
    tuple_elem([(P(1), P(2))])
    gx = [1]
    for v in gen(gx):
        print("gen:", v)
    print("nested:", nested())
    compound(1)
    kx = [1]
    kf = keep_t(kx)
    kx.append(2)
    print("generic:", kf())
    print("cost:", cost(3)())
    mixed()
    print("async:", asyncio.run(co_body([1])))
    try:
        print("er:", er_body(1))
    except Fail:
        print("er: failed")
    bag = Bag()
    print("auto_readonly:", bag.reader()())
    reseat([P(1), P(2), P(3)])
    rebind_after()
    while_unpack([(1, 2), (3, 4)])
    rebound_param(P(1), True)
    dyn_local(Sq(2))
    callable_state()


main()


# module: a module-level comprehension of lambdas over its variable, a
# reference one, and a lambda over an enclosing module lambda's parameter
mod_fs: list[Callable[[], int32]] = [lambda: i for i in range(2)]  # tpyc: warning(/'i' is bound again on each iteration/)
mod_ps = [P(1), P(2)]
mod_gs: list[Callable[[], int32]] = [lambda: q.n for q in mod_ps]  # tpyc: warning(/copies the object 'q'/) warning(/'q' is bound again/)
mod_curry: Callable[[list[int32]], Callable[[], int32]] = lambda zs: (lambda: len(zs))  # tpyc: warning(/copies the object 'zs'/)
mod_zs = [1]
mod_read = mod_curry(mod_zs)
mod_ps[0].n = 7
mod_zs.append(2)
print("module:", mod_fs[0](), mod_fs[1](), mod_gs[0](), mod_read())


# module_block: a binding in a module-level loop or `if` body is a local of
# the module's init code, not module storage, so the lambda copies it
mod_bfs: list[Callable[[], int32]] = []
for mod_i in range(2):
    mod_x = P(mod_i)
    mod_bfs.append(lambda: mod_x.n)  # tpyc: warning(/copies the object 'mod_x'/) warning(/'mod_x' is bound again/)
    mod_x.n = 10 + mod_i
mod_vfs: list[Callable[[], int32]] = []
for mod_j in range(2):
    mod_v = mod_j * 10
    mod_vfs.append(lambda: mod_v)  # tpyc: warning(/'mod_v' is bound again/)
if len(mod_bfs) > 0:
    mod_y = P(1)
    mod_yf: Callable[[], int32] = lambda: mod_y.n  # tpyc: warning(/copies the object 'mod_y'/)
    mod_y.n = 5
    print("module_block:", mod_bfs[0](), mod_bfs[1](), mod_vfs[0](), mod_vfs[1](), mod_yf())
