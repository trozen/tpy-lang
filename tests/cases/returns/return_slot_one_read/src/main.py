# Every source a body hands back -- a return, a lambda body -- is read once
# for its result slot and converted there, one section per source shape.
from typing import Any, Callable, Optional, Protocol
from tpy import (Fn, Own, Ptr, Span, SpanIter, auto_readonly, copy, dynamic,
                 error_return, int8, int32, int64, nocopy, readonly,
                 ReturnException, take_ptr)


class A:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


# copy_narrowed: copy() of a None-narrowed Optional[A] at an owning return
# copies the object the pointer names.
def copy_narrowed(a: A | None) -> Own[A]:
    if a is not None:
        return copy(a)  # tpyc: ok
    return A(0)


def inc(i: int32) -> int32:
    return i + 1


# own_callable: an Own[Callable] result is the Callable slot.
def own_callable() -> Own[Callable[[int32], int32]]:
    return inc  # tpyc: ok


# own_closure: a closure local at the same result.
def own_closure(n: int32) -> Own[Callable[[int32], int32]]:
    f: Callable[[int32], int32] = lambda i: i + n
    return f  # tpyc: ok


# own_nested_def: a nested def returned by name at the same result.
def own_nested_def(n: int32) -> Own[Callable[[int32], int32]]:
    def g(i: int32) -> int32:
        return i * n
    return g  # tpyc: ok


class B:
    _opt: Optional[A]

    def __init__(self) -> None:
        self._opt = A(4)

    @property
    def opt(self) -> Optional[A]:
        return self._opt

    # getter_self: the getter read off self at a pointer-repr Optional return.
    def first(self) -> Optional[A]:
        return self.opt  # tpyc: ok


# getter_opt: a getter's Optional result at a pointer-repr Optional return
# is the member the getter names.
def take(b: B) -> Optional[A]:
    return b.opt  # tpyc: ok


def pick(a: A) -> A:
    return a


class K:
    a: A

    def __init__(self) -> None:
        self.a = A(1)

    def get(self) -> A:
        return self.a

    @property
    def pp(self) -> A:
        return self.a


# opt_call: a free call returning a borrow, at a pointer-repr Optional
# result, binds the object the call names.
def opt_call(a: A) -> A | None:
    return pick(a)  # tpyc: ok


# opt_method: a borrowing method's result at the Optional result.
def opt_method(k: K) -> A | None:
    return k.get()  # tpyc: ok


# opt_property: a `@property -> A` read at the Optional result.
def opt_property(k: K) -> A | None:
    return k.pp  # tpyc: ok


# opt_walrus: a walrus naming a parameter at the Optional result.
def opt_walrus(a: A) -> A | None:
    return (w := a)  # tpyc: ok


# ref_walrus: the same walrus at a reference result.
def ref_walrus(a: A) -> A:
    return (w := a)  # tpyc: ok


# param_named_self: a free function's parameter merely named `self` is an
# ordinary parameter at a borrow result.
def param_named_self(self: A) -> A:
    return self  # tpyc: ok


def through(f: Fn[[A], Own[A]], a: A) -> int32:
    return f(a).x


# none_any: None at an Any result is the boxed None, as at an Any field.
def none_any(ok: bool) -> Any:
    if not ok:
        return None  # tpyc: ok
    return 7


type Json = None | bool | int32 | str | list[Json]


# wrapper_local: a recursive union local of the result's own type moves out
# whole; a str literal lands in its member.
def wrapper_local(n: int32) -> Own[Json]:
    if n == 0:
        return "s"  # tpyc: ok
    j: Json = [1, 2]
    return j  # tpyc: ok


# union_view: a str view at a value union's owned str member is copied in.
def union_view(s: str) -> int32 | str:
    return s  # tpyc: ok


def union_opt_view(a: Optional[str]) -> int32 | str:
    assert a is not None
    return a  # tpyc: ok


# opt_str_select: a select of a parameter's view and a literal at an owned
# Optional[str] result.
def opt_str_select(a: str, c: bool) -> Optional[str]:
    return a if c else "lit"  # tpyc: ok


# cross_width: a narrowed `int8 | None` parameter widens into the result's
# optional, as it does at a field.
def cross_width(p: int8 | None) -> int32 | None:
    if p is not None:
        return p  # tpyc: ok
    return None


class U:
    u: int32 | str

    def __init__(self) -> None:
        self.u = 0

    # union_field_view: the same narrowed view written into the union field.
    def set(self, a: Optional[str]) -> None:
        if a is not None:
            self.u = a  # tpyc: ok

    def get(self) -> int32 | str:
        return self.u


class S:
    y: int32

    def __init__(self, y: int32) -> None:
        self.y = y


class HP:
    f: A

    def __init__(self) -> None:
        self.f = A(5)


# ptr_union_member: a member's storage at a pointer-variant union result is
# borrowed by address, as at the Optional result.
def ptr_union_member(h: HP) -> A | S:
    return h.f  # tpyc: ok


# ptr_union_elem: a container element at the same result.
def ptr_union_elem(xs: list[A]) -> A | S:
    return xs[0]  # tpyc: ok


# ptr_union_select: a select of two parameters at the same result.
def ptr_union_select(a: A, b: A, c: bool) -> A | S:
    return a if c else b  # tpyc: ok


# ptr_union_call: a borrowing call at the same result.
def ptr_union_call(a: A) -> A | S:
    return pick(a)  # tpyc: ok


# ptr_union_narrowed: a None-narrowed Optional parameter at the same result.
def ptr_union_narrowed(o: Optional[A], s: S) -> A | S:
    if o is not None:
        return o  # tpyc: ok
    return s


class InA:
    m: A

    def __init__(self) -> None:
        self.m = A(1)


class OutA:
    inner: InA

    def __init__(self) -> None:
        self.inner = InA()

    # nested_self_field: a record two fields under self at a borrow result.
    def rec(self) -> A:
        return self.inner.m  # tpyc: ok


class PH:
    p: Ptr[InA]

    def __init__(self, it: Ptr[InA]) -> None:
        self.p = it

    # ptr_field_member: a record through a Ptr field at a borrow result.
    def get(self) -> A:
        return self.p.m  # tpyc: ok


class Inner:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value


# narrowed_member: an isinstance-narrowed union member at a borrow result is
# the member object itself.
def narrowed_member(u: A | Inner, d: Inner) -> Inner:
    if isinstance(u, Inner):
        return u  # tpyc: ok
    return d


# ro_narrowed_member: a member narrowed out of a readonly union is the
# member object at a readonly result.
def ro_narrowed_member(u: readonly[A | Inner], d: Inner) -> readonly[Inner]:
    if isinstance(u, Inner):
        return u  # tpyc: ok
    return d


# own_elem: an element of a list the function only borrows, at an Own[A]
# result -- the copy is the declared semantic of the owned result, and the
# warning names it (CPython would hand back the element itself, so the
# section prints only the returned object).
def own_elem(xs: list[Own[A]]) -> Own[A]:
    return xs[0]  # tpyc: warning(/copies A into owned storage/)


# own_dict_elem: the same at a dict's element.
def own_dict_elem(d: dict[str, Own[A]]) -> Own[A]:
    return d["k"]  # tpyc: warning(/copies A into owned storage/)


class OE:
    def __init__(self) -> None:
        pass

    # own_elem_method: the same in a method.
    def first(self, xs: list[Own[A]]) -> Own[A]:
        return xs[0]  # tpyc: warning(/copies A into owned storage/)


# own_tuple_name: a tuple local of owned lists moves out whole, as it moves
# into a tuple field.
def own_tuple_name() -> tuple[Own[list[int32]], Own[list[int32]]]:
    a: list[int32] = []
    b: list[int32] = []
    a.append(1)
    b.append(2)
    t = (a, b)
    return t  # tpyc: ok


# own_tuple_param: an owned tuple parameter returned whole moves out.
def own_tuple_param(p: Own[tuple[A, A]]) -> Own[tuple[A, A]]:
    return p  # tpyc: ok


# own_tuple_storage_name: a storage tuple local at a whole owned result.
def own_tuple_storage_name() -> Own[tuple[A, int32]]:
    t = (A(3), 1)
    return t  # tpyc: ok


class TP:
    pair: tuple[A, A]

    def __init__(self) -> None:
        self.pair = (A(1), A(2))


# own_tuple_borrowed_local: a tuple local bound to a field's tuple copies
# each element into the owned result, warned (the section prints only the
# result).
def own_tuple_borrowed_local(h: TP) -> Own[tuple[A, A]]:
    p = h.pair
    return p  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


# tuple_elem_read: an element of a list of tuples at a value tuple result.
def tuple_elem_read(xs: list[tuple[int64, int32]]) -> tuple[int64, int32]:
    return xs[1]  # tpyc: ok


# tuple_elem_local: the same off a local list.
def tuple_elem_local() -> tuple[int64, int32]:
    xs: list[tuple[int64, int32]] = [(5, 6), (7, 8)]
    return xs[1]  # tpyc: ok


# own_tuple_backedge: a tuple local built in a loop before a later rebind of
# its element's name copies that element into the owned result, warned (the
# section prints only the result).
def own_tuple_backedge(w: A) -> Own[tuple[A, int32]]:
    b = A(1)
    i = 0
    while i < 2:
        if i == 1:
            q = (b, 0)  # tpyc: warning(/copies A into owned storage/)
            return q
        b = w
        i += 1
    return (A(0), 0)


# own_tuple_captured: a storage tuple local a closure reads is copied out
# and warned, as its scalar twin is: the closure may still read it.
def own_tuple_captured() -> Own[tuple[A, int32]]:
    t = (A(3), 1)

    def peek() -> int32:
        return t[1]
    print("own_tuple_captured peek", peek())
    return t  # tpyc: warning(/copies tuple\[A, int32\] into owned storage/)


@nocopy
class NP:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


# staged_own_opt: an `Own` parameter returned from a try body is staged
# before the finally runs and moves there, as its own `return` would (a
# @nocopy payload has no copy to take instead).
def staged_own_opt(x: Own[Optional[NP]]) -> Own[Optional[NP]]:
    try:
        return x  # tpyc: ok
    finally:
        print("staged_own_opt finally")


class Ctx:
    def __init__(self) -> None:
        pass

    def __enter__(self) -> int32:
        return 1

    def __exit__(self, kind, value, tb) -> None:
        print("with_own_opt exit")


# with_own_opt: the same from a `with` body.
def with_own_opt(x: Own[Optional[NP]]) -> Own[Optional[NP]]:
    with Ctx():
        return x  # tpyc: ok


class E(Exception, ReturnException):
    pass


# er_own_opt: the same from an @error_return body (built, not called: no
# caller binds an Optional @error_return result,
# BUGS.md#optional-result-error-return-bind).
@error_return(E)
def er_own_opt(x: Own[Optional[NP]], c: bool) -> Own[Optional[NP]]:
    if c:
        raise E()
    try:
        return x  # tpyc: ok
    finally:
        print("er_own_opt finally")


# staged_except: the same from an except handler of a try with a finally.
def staged_except(x: Own[Optional[NP]], fail: bool) -> Own[Optional[NP]]:
    try:
        if fail:
            raise ValueError("fail")
        return None
    except ValueError:
        return x  # tpyc: ok
    finally:
        print("staged_except finally")


# captured_own_opt: a nested def returning the `Own[P | None]` parameter it
# captures moves it out of the binding its result is (a @nocopy payload has
# no copy to take), so only one call may take it
# (BUGS.md#nested-def-captured-own-return-copies).
def captured_own_opt(x: Own[Optional[NP]]) -> Own[Optional[NP]]:
    def inner() -> Own[Optional[NP]]:
        return x  # tpyc: ok
    return inner()


# captured_own_list: a nested def returning the list parameter it captures
# copies it each call (BUGS.md#nested-def-captured-own-return-copies), so a
# second call and a later read of the parameter see every element. The
# never-consumed warning is right: no call takes the parameter over, each
# copies it, so the transfer `Own` asks of the caller goes unused.
def captured_own_list(x: Own[list[int32]]) -> int32:  # tpyc: warning(/never consumed/)
    def inner() -> Own[list[int32]]:
        return x  # tpyc: ok
    a = inner()
    b = inner()
    print("captured_own_list", len(a), len(b), len(x))
    return len(a)


# captured_own_list_lambda: the lambda twin copies the captured list each
# call the same way, warned as a lambda's `Own` result copies its borrowed
# body, so a second call and a later read of the parameter see every element.
def captured_own_list_lambda(x: Own[list[int32]]) -> int32:  # tpyc: warning(/never consumed/)
    # the lambda copies `x` when it is created, and each call copies that
    # capture into the `Own` result; called at once, it prints the same
    f: Callable[[], Own[list[int32]]] = lambda: x  # tpyc: warning(/copies the object 'x'/) warning(/copies list\[int32\] into owned storage/)
    a = f()
    b = f()
    print("captured_own_list_lambda", len(a), len(b), len(x))
    return len(a)


@dynamic
class Shape(Protocol):
    def bump(self) -> None: ...
    def get(self) -> int32: ...


class Circle(Shape):
    r: int32

    def __init__(self, r: int32) -> None:
        self.r = r

    def bump(self) -> None:
        self.r += 1

    def get(self) -> int32:
        return self.r


class Sq:
    r: int32

    def __init__(self, r: int32) -> None:
        self.r = r

    def bump(self) -> None:
        self.r += 1

    def get(self) -> int32:
        return self.r


# dyn_local: an inheriting record local at a @dynamic owner result moves in.
def dyn_local() -> Own[Shape]:
    c = Circle(2)
    return c  # tpyc: ok


# dyn_own_param: an owned structural parameter is forwarded into the owner.
def dyn_own_param(q: Own[Sq]) -> Own[Shape]:
    return q  # tpyc: ok


# dyn_struct_local: a structural local moves into its adapter.
def dyn_struct_local() -> Own[Shape]:
    q = Sq(3)
    return q  # tpyc: ok


# dyn_borrowed: a borrowed parameter is copied into the owner, with the
# warning every owning slot gives (the section prints only the result).
def dyn_borrowed(s: Sq) -> Own[Shape]:
    return s  # tpyc: warning(/copies Sq into owned storage/)


class N:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


class H:
    o: Optional[A]

    def __init__(self) -> None:
        self.o = None

    # reseated_field: a reseated record local lifts into an Optional field,
    # copying the record, as at an Optional result.
    def pick(self, a: A, b: A, c: bool) -> None:
        p = a
        if c:
            p = b
        self.o = p  # tpyc: warning(/copies A into field/)


class SR:
    items: list[A]

    def __init__(self) -> None:
        self.items = [A(1), A(2)]

    # span_records: a Span of records handed out off a list field.
    @auto_readonly
    def __span__(self) -> Span[auto_readonly[A]]:
        return self.items  # tpyc: ok

    @auto_readonly
    def __iter__(self) -> SpanIter[auto_readonly[A]]:
        return SpanIter(self.__span__())


def json_text(j: Json) -> str:
    match j:
        case str():
            return j
        case list():
            return "list" + str(len(j))
        case _:
            return "other"


# gen_tuple_name: a generic tuple parameter at an owned tuple result copies
# each element in (the warned copy is the intended semantics; the one copy
# is warned twice, BUGS.md#generic-tuple-own-return-double-hedge).
def gen_tuple_name[T](x: tuple[T, int32]) -> Own[tuple[T, int32]]:
    return x  # tpyc: warning(/may copy tuple\[T, int32\]/) warning(/may copy T into owned storage \(tuple element 0\)/)


def gen_pair[T](a: T) -> tuple[T, int32]:
    return (a, 1)


# gen_tuple_call: a call handing back a generic borrow-form tuple at an owned
# tuple result copies each element in (the warned copy is the intended
# semantics).
def gen_tuple_call[T](a: T) -> Own[tuple[T, int32]]:
    return gen_pair(a)  # tpyc: warning(/may copy T into owned storage \(tuple element 0\)/)


# gen_tuple_pair: a two-T tuple parameter copies both elements in (warned per
# element and whole, BUGS.md#generic-tuple-own-return-double-hedge).
def gen_tuple_pair[T](x: tuple[T, T]) -> Own[tuple[T, T]]:
    return x  # tpyc: warning(/may copy tuple\[T, T\]/) warning(/may copy T into owned storage \(tuple element 0\)/) warning(/may copy T into owned storage \(tuple element 1\)/)


# gen_tuple_loop: a loop variable over generic tuples, and the fallback
# parameter, each copied in at the owned result (warned twice each,
# BUGS.md#generic-tuple-own-return-double-hedge).
def gen_tuple_loop[T](xs: list[tuple[T, int32]],
                      d: tuple[T, int32]) -> Own[tuple[T, int32]]:
    for x in xs:
        if x[1] > 1:
            return x  # tpyc: warning(/may copy tuple\[T, int32\]/) warning(/may copy T into owned storage \(tuple element 0\)/)
    return d  # tpyc: warning(/may copy tuple\[T, int32\]/) warning(/may copy T into owned storage \(tuple element 0\)/)


# gen_tuple_select: a select of two generic tuple parameters copies the
# chosen one in.
def gen_tuple_select[T](x: tuple[T, int32], y: tuple[T, int32],
                        c: bool) -> Own[tuple[T, int32]]:
    return x if c else y  # tpyc: warning(/may copy T into owned storage \(tuple element 0\)/)


def gen_own_pair[T](a: T) -> Own[tuple[T, int32]]:
    return (a, 2)  # tpyc: warning(/may copy T into owned storage \(tuple element 0\)/)


# gen_own_call: a call handing back a generic OWNED tuple at an owned tuple
# result is the fresh storage its declaration says, returned as it is.
def gen_own_call[T](a: T) -> Own[tuple[T, int32]]:
    return gen_own_pair(a)  # tpyc: ok


# own_tuple_param_lists: a tuple local holding the caller's lists at an
# owning tuple result copies each element in, as sema warns per element.
def own_tuple_param_lists(xs: list[int32], ys: list[int32]) -> tuple[Own[list[int32]], Own[list[int32]]]:
    t = (xs, ys)
    return t  # tpyc: warning(/copies list\[int32\] into owned storage \(tuple element 0\)/) warning(/copies list\[int32\] into owned storage \(tuple element 1\)/)


def mk_borrow(a: A) -> tuple[A, int32]:
    return (a, 1)


# borrow_tuple_call: a borrow-form tuple call result at an owned tuple result
# copies the element sema declared (warned per element).
def borrow_tuple_call(a: A) -> Own[tuple[A, int32]]:
    return mk_borrow(a)  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


# elem_mixed_result: a container element at a mixed own / borrow result
# copies the owned element (warned) and keeps borrowing the other.
def elem_mixed_result(xs: list[tuple[A, A]]) -> tuple[Own[A], A]:
    return xs[0]  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


def main() -> None:
    src = A(3)
    c = copy_narrowed(src)
    c.x = 9
    print("copy_narrowed", src.x, c.x)
    print("own_callable", own_callable()(1))
    print("own_closure", own_closure(2)(1))
    print("own_nested_def", own_nested_def(3)(2))
    b = B()
    v = take(b)
    if v is not None:
        v.x = 7
    w = b.first()
    if w is not None:
        print("getter_opt", w.x)
    a = A(1)
    r1 = opt_call(a)
    if r1 is not None:
        r1.x = 11
    print("opt_call", a.x)
    k = K()
    r2 = opt_method(k)
    if r2 is not None:
        r2.x = 12
    print("opt_method", k.a.x)
    r3 = opt_property(k)
    if r3 is not None:
        r3.x = 13
    print("opt_property", k.a.x)
    r4 = opt_walrus(a)
    if r4 is not None:
        r4.x = 14
    print("opt_walrus", a.x)
    ref_walrus(a).x = 15
    print("ref_walrus", a.x)
    param_named_self(a).x = 16
    print("param_named_self", a.x)
    flag = False
    # lambda_select: a record select of two fresh arms is the lambda's
    # owned result, as at a nested def's `return`.
    print("lambda_select", through(lambda q: A(0) if flag else A(1), A(2)))  # tpyc: ok
    # sorted_key: a str field at a key lambda's owned str result.
    nodes = [N("pear"), N("apple")]
    print("sorted_key", [n.name for n in sorted(nodes, key=lambda n: n.name)])  # tpyc: ok
    n0 = none_any(False)
    n1 = none_any(True)
    print("none_any", n0 is None, n1 is None)
    print("wrapper_local", json_text(wrapper_local(0)),
          json_text(wrapper_local(1)))
    u1 = union_view("ab")
    u2 = union_opt_view("cd")
    if isinstance(u1, str) and isinstance(u2, str):
        print("union_view", u1, u2)
    print("opt_str_select", opt_str_select("ij", True), opt_str_select("kl", False))
    print("cross_width", cross_width(3), cross_width(None))
    hu = U()
    hu.set("ef")
    uv = hu.get()
    if isinstance(uv, str):
        print("union_field_view", uv)
    hp = HP()
    m = ptr_union_member(hp)
    if isinstance(m, A):
        m.x = 21
    print("ptr_union_member", hp.f.x)
    elems: list[A] = [A(30)]
    pe = ptr_union_elem(elems)
    if isinstance(pe, A):
        pe.x = 22
    print("ptr_union_elem", elems[0].x)
    sa = A(40)
    ss = S(41)
    sb = A(0)
    ps = ptr_union_select(sa, sb, True)
    if isinstance(ps, A):
        ps.x = 23
    print("ptr_union_select", sa.x)
    pc = ptr_union_call(sa)
    if isinstance(pc, A):
        pc.x = 24
    print("ptr_union_call", sa.x)
    pn = ptr_union_narrowed(sa, ss)
    if isinstance(pn, A):
        pn.x = 25
    print("ptr_union_narrowed", sa.x)
    oa = OutA()
    oa.rec().x = 26
    print("nested_self_field", oa.inner.m.x)
    ia = InA()
    ph = PH(take_ptr(ia))
    ph.get().x = 27
    print("ptr_field_member", ia.m.x)
    i = Inner(2)
    nu: A | Inner = i
    d = Inner(9)
    narrowed_member(nu, d).value = 5
    print("narrowed_member", i.value)
    print("ro_narrowed_member", ro_narrowed_member(nu, d).value)
    oes: list[A] = [A(30)]
    oe = own_elem(oes)
    oe.x = 31
    print("own_elem", oe.x)
    od: dict[str, A] = {"k": A(32)}
    ode = own_dict_elem(od)
    ode.x = 33
    print("own_dict_elem", ode.x)
    omes: list[A] = [A(30)]
    ome = OE().first(omes)
    ome.x = 34
    print("own_elem_method", ome.x)
    l1, l2 = own_tuple_name()
    print("own_tuple_name", len(l1), len(l2))
    t1, t2 = own_tuple_param((A(1), A(2)))
    t1.x = 5
    print("own_tuple_param", t1.x, t2.x)
    t3, t4 = own_tuple_storage_name()
    print("own_tuple_storage_name", t3.x, t4)
    t5, t6 = own_tuple_borrowed_local(TP())
    t5.x = 9
    print("own_tuple_borrowed_local", t5.x, t6.x)
    txs: list[tuple[int64, int32]] = [(1, 2), (3, 4)]
    print("tuple_elem_read", tuple_elem_read(txs))
    print("tuple_elem_local", tuple_elem_local())
    bq, bn = own_tuple_backedge(A(4))
    print("own_tuple_backedge", bq.x, bn)
    cq, cn = own_tuple_captured()
    print("own_tuple_captured", cq.x, cn)
    s1 = staged_own_opt(NP(1))
    print("staged_own_opt", s1.v if s1 is not None else -1)
    s2 = with_own_opt(NP(2))
    print("with_own_opt", s2.v if s2 is not None else -1)
    s3 = staged_except(NP(3), True)
    print("staged_except", s3.v if s3 is not None else -1)
    s4 = captured_own_opt(NP(4))
    print("captured_own_opt", s4.v if s4 is not None else -1)
    captured_own_list([1, 2, 3])
    captured_own_list_lambda([4, 5])
    dl = dyn_local()
    dl.bump()
    print("dyn_local", dl.get())
    dp = dyn_own_param(Sq(5))
    dp.bump()
    print("dyn_own_param", dp.get())
    ds = dyn_struct_local()
    ds.bump()
    print("dyn_struct_local", ds.get())
    db = dyn_borrowed(Sq(7))
    db.bump()
    print("dyn_borrowed", db.get())
    h = H()
    ra = A(1)
    rb = A(2)
    h.pick(ra, rb, True)
    ho = h.o
    print("reseated_field", ho.x if ho is not None else -1)
    sr = SR()
    for e in sr:
        e.x += 10
    print("span_records", sr.items[0].x, sr.items[1].x)
    ga, gn = gen_tuple_name((A(40), 3))
    print("gen_tuple_name", ga.x, gn)
    gc, gcn = gen_tuple_call(A(41))
    print("gen_tuple_call", gc.x, gcn)
    go, gon = gen_own_call(A(42))
    go.x += 1
    print("gen_own_call", go.x, gon)
    gpa, gpb = gen_tuple_pair((A(1), A(2)))
    gpa.x += 10
    print("gen_tuple_pair", gpa.x, gpb.x)
    gla, gln = gen_tuple_loop([(A(3), 1), (A(4), 2)], (A(5), 0))
    gla.x += 10
    print("gen_tuple_loop", gla.x, gln)
    gsa, gsn = gen_tuple_select((A(6), 1), (A(7), 2), True)
    gsb, gsm = gen_tuple_select((A(6), 1), (A(7), 2), False)
    gsa.x += 10
    gsb.x += 10
    print("gen_tuple_select", gsa.x, gsn, gsb.x, gsm)
    pl: list[int32] = [5]
    ql: list[int32] = [6, 7]
    ul, vl = own_tuple_param_lists(pl, ql)
    print("own_tuple_param_lists", len(ul), len(vl))
    bta, btn = borrow_tuple_call(A(50))
    print("borrow_tuple_call", bta.x, btn)
    mys = [(A(3), A(4))]
    mo, mb = elem_mixed_result(mys)
    mb.x = 40  # the borrowed element is the list's own object
    print("elem_mixed_result", mo.x, mys[0][1].x)


main()
