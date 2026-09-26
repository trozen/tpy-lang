# A temporary ELEMENT of a tuple literal passed to a generator/coroutine is
# hoisted past the statement; the lvalue element stays aliased on both sides,
# and lends its storage exactly as the same scalar argument would. An OWNED
# element lives in the tuple temporary, like a temporary scalar argument.
# An element whose type cannot hold what the result points at backs nothing.
import asyncio
from typing import Iterator

from tpy import Own, Ptr, StrView, readonly


class A:
    def __init__(self, v: int) -> None:
        self.v = v


class B:
    def __init__(self, w: int) -> None:
        self.w = w


def pair(p: tuple[A, A]) -> Iterator[int]:
    yield p[0].v
    p[1].v += 1
    yield p[1].v


def pairn(p: tuple[int, A]) -> Iterator[int]:
    yield p[0] + p[1].v


def bump(p: tuple[A, int]) -> None:
    p[0].v += p[1]


def opt_pair(p: tuple[A | None, A]) -> Iterator[int]:
    p[1].v += 1
    yield p[1].v


class G:
    def pair(self, p: tuple[A, A]) -> Iterator[int]:
        yield p[0].v
        p[1].v += 1
        yield p[1].v


async def co(p: tuple[A, A]) -> int:
    await asyncio.sleep(0)
    p[1].v += 1
    return p[0].v * 100 + p[1].v


class H:
    xs: list[int]

    def __init__(self) -> None:
        r = A(5)
        # call inside a constructor body
        self.xs = [x for x in pair((A(21), r))]  # tpyc: ok


def total(p: tuple[A, A]) -> int:
    p[1].v += 1
    return p[0].v * 100 + p[1].v


def free_call() -> None:
    r = A(5)
    # free generator call: the temporary element hoists
    for x in pair((A(7), r)):  # tpyc: ok
        print("free", x)
        r.v += 10
    print("free r", r.v)


def factory_method() -> None:
    r = A(5)
    # generator METHOD call
    for x in G().pair((A(15), r)):  # tpyc: ok
        print("method", x)
        r.v += 10
    print("method r", r.v)


def optional_element() -> None:
    r = A(5)
    # a pointer-repr Optional element slot takes the same hoist
    for x in opt_pair((A(9), r)):  # tpyc: ok
        print("opt", x)
    print("opt r", r.v)


def comprehension_iterable() -> None:
    r = A(5)
    # comprehension iterable
    xs = [x for x in pair((A(17), r))]  # tpyc: ok
    print("comp", xs, r.v)


def held_handle() -> None:
    r = A(5)
    # a held handle outlives the statement that built the tuple
    it = pair((A(4), r))  # tpyc: ok
    junk = [A(i) for i in range(50)]
    r.v = 6
    for x in it:
        print("held", x, len(junk))
    print("held r", r.v)


def outer() -> Iterator[int]:
    r = A(3)
    # caller inside a generator body: the element seats on a frame field
    for x in pair((A(4), r)):  # tpyc: ok
        yield x
        r.v += 10
    yield r.v


def generator_body() -> None:
    for x in outer():
        print("gen-body", x)


async def await_caller() -> int:
    r = A(3)
    # awaited inside an async body
    t = await co((A(4), r))  # tpyc: ok
    return t * 1000 + r.v


async def task_caller() -> int:
    r = A(3)
    # create_task: the task outlives the statement
    task = asyncio.create_task(co((A(5), r)))  # tpyc: ok
    r.v = 6
    junk = [A(i) for i in range(50)]
    t = await task
    return t * 1000 + r.v + len(junk)


def async_positions() -> None:
    print("await", asyncio.run(await_caller()))
    print("task", asyncio.run(task_caller()))
    r = A(5)
    # asyncio.run keeps the same hoist
    print("run", asyncio.run(co((A(19), r))), r.v)  # tpyc: ok


def sync_callee() -> None:
    r = A(5)
    # inverse: a sync callee keeps the full-expression source tuple
    print("sync", total((A(7), r)), r.v)  # tpyc: ok


def ctor_and_closure() -> None:
    print("ctor", H().xs)
    r = A(1)

    def f() -> Own[list[int]]:
        # call inside a nested closure over the enclosing local
        return list(pair((A(23), r)))  # tpyc: ok
    print("closure", f(), r.v)


def invalidated_element() -> None:
    r = A(5)
    xs = [A(7), A(8)]
    it = pair((xs[0], r))
    for x in it:
        print("invalidate", x, len(xs))
        break
    # the frame borrows xs[0] through the tuple, as it would a scalar xs[0]
    xs.append(A(9))  # tpyc: warning(/Mutation of 'xs' while borrowed/)
    for x in it:
        print("invalidate", x, r.v)


def owned_element() -> None:
    ns = [1, 2]
    r = A(5)
    it = pairn((ns[0], r))
    # an owned (int) element is copied into the frame's tuple: it lends nothing
    ns.append(3)  # tpyc: ok
    print("owned", list(it), len(ns))


def edge_free(a: A) -> int:
    # free function: the element lends the caller's param to a mutating callee
    bump((a, 1))  # tpyc: ok
    return a.v


class E:
    def edge_method(self, a: A) -> int:
        # method body
        bump((a, 2))  # tpyc: ok
        return a.v


def edge_gen(a: A) -> Iterator[int]:
    # generator body
    bump((a, 3))  # tpyc: ok
    yield a.v


def edge_frame(a: A) -> int:
    # the mutating callee is itself a generator frame
    return sum(pair((A(1), a)))  # tpyc: ok


def mutation_edge() -> None:
    a = A(0)
    # each write through the tuple element is seen through `a` afterwards
    print("edge free", edge_free(a), a.v)
    print("edge method", E().edge_method(a), a.v)
    print("edge gen", list(edge_gen(a)), a.v)
    print("edge frame", edge_frame(a), a.v)


def loop_gen() -> Iterator[int]:
    cs = [A(1), A(2)]
    for c in cs:
        # loop variable inside a generator body
        bump((c, 10))  # tpyc: ok
        yield c.v
    yield cs[0].v


def loop_var_element() -> None:
    cs = [A(1), A(2)]
    for c in cs:
        # a loop variable lent through the tuple to a mutating callee
        bump((c, 1))  # tpyc: ok
    print("loop local", cs[0].v, cs[1].v)
    d = {1: A(5)}
    for v in d.values():
        # a dict-view loop variable
        bump((v, 2))  # tpyc: ok
    print("loop dict", d[1].v)
    print("loop gen", list(loop_gen()))


class K:
    def bump(self, p: tuple[A, int]) -> None:
        p[0].v += p[1]


async def abump(p: tuple[A, int]) -> None:
    await asyncio.sleep(0)
    p[0].v += p[1]


def meth_callee(k: K, a: A) -> int:
    # a mutating METHOD callee
    k.bump((a, 1))  # tpyc: ok
    return a.v


async def async_callee(a: A) -> int:
    # an awaited mutating coroutine callee
    await abump((a, 10))  # tpyc: ok
    return a.v


def callee_kinds() -> None:
    a = A(0)
    print("callee method", meth_callee(K(), a), a.v)
    print("callee async", asyncio.run(async_callee(a)), a.v)


class Holder[T]:
    def __init__(self, value: Own[T]) -> None:
        self.value = value

    def pair(self) -> tuple[T, int]:
        return (self.value, 1)


def generic_accessor() -> None:
    h = Holder(A(1))
    t = h.pair()
    # the returned element borrows `h.value` at a reference T: the write lands
    t[0].v = 42  # tpyc: ok
    print("generic accessor", h.value.v)


def bump_ro(p: tuple[A, readonly[B]]) -> None:
    p[0].v += p[1].w


def ro_outer(a: A, b: B) -> None:
    # a readonly element grants no write: `b` stays a const borrow
    bump_ro((a, b))  # tpyc: ok


def ptr_elem(ps: list[Ptr[A]]) -> None:
    # a Ptr element is a copied pointer VALUE: the write reaches the pointee,
    # never `ps`, which stays a const borrow
    bump((ps[0], 1000))  # tpyc: ok


def peek[T](p: tuple[T, int]) -> int:
    return p[1]


def generic_slot(a: A) -> int:
    # an open-T element slot binds a mutable borrow, as a scalar T param does
    return peek((a, 2))  # tpyc: ok


def element_write_grant() -> None:
    a = A(1)
    bs = [B(4)]
    for b in bs:
        ro_outer(a, b)
    a.v += 100
    print("ro elem", a.v, bs[0].w)
    items = [A(1)]
    ps: list[Ptr[A]] = []
    ps.append(items[0])
    ptr_elem(ps)
    print("ptr elem", items[0].v)
    g = A(3)
    n = generic_slot(g)
    g.v += 1
    print("generic slot", n, g.v)


def sv_first(t: tuple[str, int]) -> StrView:
    return t[0]


def sv_inner(t: tuple[tuple[str, int], int]) -> StrView:
    return t[0][0]


def sv_first1(s: str) -> StrView:
    return s


def owned_element_view(s: str, p: tuple[str, int]) -> None:
    # the tuple temporary OWNS its copy of `s`: a view of it would dangle at
    # the end of the statement, so the local owns, like `sv_first1(s + "-tmp")`
    v = sv_first((s, 1))  # tpyc: ok
    w = sv_first(("literal-element-long-enough-to-defeat-sso", 2))  # tpyc: ok
    n = sv_inner(((s, 3), 4))  # tpyc: ok
    # scalar twins: a view of the param stays one, of a temporary owns
    x = sv_first1(s)  # tpyc: ok
    y = sv_first1(s + "-tmp")  # tpyc: ok
    # inverse: a tuple NAME is the caller's storage, so the view stays one
    z = sv_first(p)  # tpyc: ok
    junk = [str(i) * 40 for i in range(8)]
    print("owned view", v, w, n, len(junk))
    print("owned view twins", x, y, z)


def owned_view_gen(s: str) -> Iterator[str]:
    # generator body
    v = sv_first((s, 1))  # tpyc: ok
    junk = [str(i) * 40 for i in range(8)]
    yield str(v) + str(len(junk))


def owned_element_views() -> None:
    s = "owned-element-source-long-enough-to-defeat-sso"
    owned_element_view(s, ("owned-element-param-long-enough-to-defeat-sso", 6))
    print("owned view gen", list(owned_view_gen(s)))


class Named:
    reads: int

    def __init__(self, name: str) -> None:
        self.reads = 0
        self.name = name


def nm_opt(t: tuple[Named, int | None]) -> StrView:
    t[0].reads = t[0].reads + 1
    return t[0].name


def fst[T](t: tuple[T, str]) -> T:
    return t[0]


def unbacked_return(n: Named) -> StrView:
    # return: an `int | None` element holds nothing the view can point into
    return nm_opt((n, None))  # tpyc: ok


def unbacked_local(n: Named, s: str) -> None:
    # the view of the `Named` element's field stays a view
    v = nm_opt((n, 3))  # tpyc: ok type(StrView)
    a = A(1)
    # a `str` element cannot hold the `A` the result is: no temporary warning
    x = fst((a, s))  # tpyc: ok
    x.v += 1
    print("unbacked local", v, unbacked_return(n), a.v, n.reads)


class U:
    def bump(self, a: A, s: str) -> int:
        # method body
        x = fst((a, s))  # tpyc: ok
        x.v += 1
        return a.v


def unbacked_elements() -> None:
    unbacked_local(Named("unbacked-name-long-enough-to-defeat-sso"), "s")
    a = A(5)
    print("unbacked method", U().bump(a, "s"), a.v)


# module-level call
MR = A(5)
for MX in pair((A(25), MR)):  # tpyc: ok
    print("module", MX)
print("module r", MR.v)


def main() -> None:
    free_call()
    factory_method()
    optional_element()
    comprehension_iterable()
    held_handle()
    generator_body()
    async_positions()
    sync_callee()
    ctor_and_closure()
    invalidated_element()
    owned_element()
    mutation_edge()
    loop_var_element()
    callee_kinds()
    generic_accessor()
    element_write_grant()
    owned_element_views()
    unbacked_elements()


main()
