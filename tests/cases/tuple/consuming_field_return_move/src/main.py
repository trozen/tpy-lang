# A consuming method moves `self.<field>` out at the receiver's last use
# (several distinct fields that point nowhere in one `return`), outside a
# `for`, `try`, `with` or `match` and with no local whose destruction may
# read a field; any other field read borrows, which an owning slot copies and
# warns about.
import asyncio
from enum import Enum
from typing import Self, Callable, Iterator
from tpy import int32, Own, copy, char, Ptr


class Color(Enum):
    RED = 1
    BLUE = 2


class P:
    xs: list[int32]

    def __init__(self, x: int32) -> None:
        self.xs = [x]

    def n(self) -> int32:
        return len(self.xs)


class O:
    p: P

    def __init__(self) -> None:
        self.p = P(0)


class Cm:
    def __enter__(self) -> int32:
        return 0

    def __exit__(self, et, ev, tb) -> None:
        pass


class Held:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __enter__(self) -> int32:
        return self.v

    def __exit__(self, et, ev, tb) -> None:
        pass


def take_ro(t: Own[tuple[P, P]]) -> int32:
    a, b = t
    return len(a.xs)


def take_mut(t: Own[tuple[P, P]]) -> int32:
    a, b = t
    a.xs.append(9)
    return len(a.xs)


def keep_t(t: Own[tuple[P, P]]) -> int32:
    a, b = t
    return len(a.xs) + len(b.xs)


def first_len(t: tuple[P, P]) -> int32:
    a, b = t
    return len(a.xs)


def take_both(t: Own[tuple[P, P]]) -> int32:
    a, b = t
    return len(a.xs) * 10 + len(b.xs)


def take_peek(t: Own[tuple[P, P]], q: Ptr[P]) -> int32:
    a, b = t
    return len(a.xs) * 10 + q.n()


def keep_p(p: Own[P]) -> int32:
    kept = [p]
    return len(kept[0].xs)


def gen_of(p: P) -> Iterator[int32]:
    try:
        yield 1
    finally:
        print("loop_free_generator finally", len(p.xs))


def run_twice(f: Callable[[], int32]) -> int32:
    return f() * 10 + f()


def step(g: Iterator[int32]) -> None:
    try:
        next(g)
    except StopIteration:
        pass


class Guard:
    p: Ptr[P]
    label: str

    def __init__(self, p: Ptr[P], label: str) -> None:
        self.p = p
        self.label = label

    def __del__(self) -> None:
        print(self.label, "del", self.p.n())


GP = P(7)


class H:
    a: P
    b: P
    n: int
    c: char

    def __init__(self) -> None:
        self.a = P(1)
        self.b = P(2)
        self.n = 10 ** 20
        self.c = char("z")

    def count(self) -> int32:
        return len(self.a.xs)

    @property
    def first_len(self) -> int32:
        return len(self.a.xs)

    def gen(self) -> Iterator[int32]:
        try:
            yield 1
            yield 2
        finally:
            print("local_generator finally", len(self.a.xs))

    def labeled_gen(self, label: str) -> Iterator[int32]:
        try:
            yield 1
            yield 2
        finally:
            print(label, "finally", len(self.a.xs))

    def __enter__(self) -> int32:
        return 0

    def __exit__(self, et, ev, tb) -> None:
        print("with_self exit", len(self.a.xs))

    # after: a field read before a later statement reads it again borrows
    def after(self: Own[Self]) -> int32:
        r = take_ro((self.a, self.b))  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)
        return r * 10 + len(self.a.xs)

    # last_use: the return's own fields move
    def last_use(self: Own[Self]) -> int32:
        return take_mut((self.a, self.b))  # tpyc: ok

    # bare_return: a returned field moves
    def bare_return(self: Own[Self]) -> int:
        return self.n  # tpyc: ok

    # loop_return: any enclosing `for` keeps the copy, though `range(2)`
    # cannot read the field -- the conservative guard (narrowing it: TODO.md
    # "Consuming-method field moves: the teardown stopgap")
    def loop_return(self: Own[Self]) -> int32:
        for i in range(2):
            if i == 1:
                return take_ro((self.a, self.b))  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)
        return 0

    # try_return: the `finally` reads the field after the return
    def try_return(self: Own[Self]) -> int32:
        try:
            return take_ro((self.a, self.b))  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)
        finally:
            print("try_return finally", len(self.a.xs))

    # try_return_scalar: a returned scalar field is not moved from either
    def try_return_scalar(self: Own[Self]) -> int:
        try:
            return self.n  # tpyc: ok
        finally:
            print("try_return_scalar finally", self.n)

    # with_return: any enclosing `with` keeps the copy, though `Cm()`
    # cannot read `self` -- the same guard and TODO entry
    def with_return(self: Own[Self]) -> int32:
        with Cm():
            return take_ro((self.a, self.b))  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)

    # same_field_twice: the operands have no fixed order, so neither moves
    # (both members are read: a moved member would print 0)
    def same_field_twice(self: Own[Self]) -> int32:
        return take_both((self.a, self.a))  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)

    # whole_self_in_return: a method call on `self` may read any field
    def whole_self_in_return(self: Own[Self]) -> int32:
        return take_ro((self.a, self.b)) + self.count()  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)

    # nonscalar_local_in_return: `v` aliases the field
    def nonscalar_local_in_return(self: Own[Self]) -> int32:
        v = self.a
        return take_ro((self.a, self.b)) + len(v.xs)  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)

    # scalar_local_ok: a scalar local cannot alias the field
    def scalar_local_ok(self: Own[Self]) -> int32:
        n = 3
        return take_mut((self.a, self.b)) + n  # tpyc: ok

    # field_store: a store outside a return copies, as in an ordinary method
    def field_store(self: Own[Self], o: O) -> int32:
        o.p = self.a  # tpyc: warning(/copies P into field/)
        return len(o.p.xs) + len(self.a.xs)

    # escape_copy: the explicit copy() member needs no warning; the bare one warns
    def escape_copy(self: Own[Self]) -> int32:
        r = take_mut((copy(self.a), self.b))  # tpyc: warning(/tuple element 1\)/)
        return r * 10 + len(self.a.xs)

    # nested_def_return: a nested def may run twice, so its return never moves
    def nested_def_return(self: Own[Self]) -> int:
        def g() -> int:
            return self.n  # tpyc: ok
        return g() + g()

    # lambda_body: a lambda may run twice, so it is not the method's own body
    # and its copy warning offers no final-`return` move
    def lambda_body(self: Own[Self]) -> int32:
        r = run_twice(lambda: take_ro((self.a, self.b)))  # tpyc: warning(/tuple element 0\); use copy\(\) to make this explicit$/) warning(/tuple element 1\); use copy\(\) to make this explicit$/)
        return r

    # nested_def_in_return: `peek` captures `self` and reads the field after
    # the call, so the members copy
    def nested_def_in_return(self: Own[Self]) -> int32:
        def peek() -> int32:
            return len(self.a.xs)
        return take_ro((self.a, self.b)) * 10 + peek()  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)

    # while_return: nothing of a `while` runs after its return, so the fields move
    def while_return(self: Own[Self]) -> int32:
        while True:
            return take_mut((self.a, self.b))  # tpyc: ok

    # try_handler_return: the `finally` reads the field after a handler's return
    def try_handler_return(self: Own[Self]) -> int32:
        try:
            raise ValueError("boom")
        except ValueError:
            return take_ro((self.a, self.b))  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)
        finally:
            print("try_handler_return finally", len(self.a.xs))

    # try_else_return: a return in a try's `else` is inside the `try`, and
    # any enclosing `try` keeps the copy -- the same guard and TODO entry (a
    # `finally` reading the field after it is
    # BUGS.md#try-else-body-decl-crossed-by-goto; the trailing return works
    # around the return-path analysis not crediting the `else`)
    def try_else_return(self: Own[Self]) -> int32:
        try:
            print("try_else_return body")
        except ValueError:
            return 0
        else:
            return take_ro((self.a, self.b))  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)
        return -1

    # comprehension_in_return: a comprehension's names are not proven scalar
    def comprehension_in_return(self: Own[Self]) -> int32:
        return take_ro((self.a, self.b)) + len([i for i in range(2)])  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)

    # walrus_in_return: the teardown proof is the whole body's, so the
    # scalar a walrus binds after the read cannot read a field and the
    # fields move
    def walrus_in_return(self: Own[Self]) -> int32:
        return take_ro((self.a, self.b)) + (k := 4)  # tpyc: ok

    # property_in_return: a property reads `self` as a whole
    def property_in_return(self: Own[Self]) -> int32:
        return take_ro((self.a, self.b)) * 10 + self.first_len  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)

    # nonscalar_param_in_return: a borrowed parameter cannot alias the
    # receiver the caller handed over, so the fields move
    def nonscalar_param_in_return(self: Own[Self], o: O) -> int32:
        return take_ro((self.a, self.b)) + len(o.p.xs)  # tpyc: ok

    # module_var_in_return: a module variable cannot hold the receiver the
    # caller handed over, so the fields move
    def module_var_in_return(self: Own[Self]) -> int32:
        return take_ro((self.a, self.b)) + len(GP.xs)  # tpyc: ok

    # scalar_kinds_ok: bool, char and enum locals and a module-level enum
    # class cannot reach the field, so the fields move
    def scalar_kinds_ok(self: Own[Self]) -> int32:
        f = True
        c = char("x")
        k = Color.RED
        return take_mut((self.a, self.b)) * 10 + (1 if f else 0) + (1 if c == "x" else 0) + (1 if k == Color.RED else 0)  # tpyc: ok

    # scalar_field_twice_ok: a second field read twice stays borrowed, but a
    # scalar cannot point at a moved field, so `a` and `b` still move
    def scalar_field_twice_ok(self: Own[Self]) -> int32:
        return take_mut((self.a, self.b)) * 10 + (1 if self.n == self.n else 0)  # tpyc: ok

    # local_generator: `g` is destroyed after the return value is built and
    # its `finally` reads `a`, so the members copy (a moved `a` would print 0)
    def local_generator(self: Own[Self]) -> int32:
        g = self.gen()
        step(g)
        return take_ro((self.a, self.b))  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)

    # local_list_in_scope: the blunt cost of the rule above -- `xs` runs no
    # code when destroyed, but only scalar, `str` and `bytes` locals are
    # proven harmless by their type, so the members copy
    def local_list_in_scope(self: Own[Self]) -> int32:
        xs = [5, 6]
        n = len(xs)
        return take_ro((self.a, self.b)) + n  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)

    # scalar_locals_only: int locals in scope, one not even in the value,
    # destroy nothing that can read a field, so the fields move
    def scalar_locals_only(self: Own[Self]) -> int32:
        k = 5
        m = k + 1
        return take_mut((self.a, self.b)) * 10 + m  # tpyc: ok

    # sibling_loop_guard: `gd` is bound only inside loop bodies, so it is not
    # in scope at the return, yet its slot is destroyed after the return
    # value is built and its `__del__` reads `a` (a moved `a` would print 0);
    # every binding anywhere in the method counts, so the members copy
    def sibling_loop_guard(self: Own[Self]) -> int32:
        for i in range(1):
            gd = Guard(self.a, "sibling_loop_guard")
        for j in range(1):
            gd = Guard(self.a, "sibling_loop_guard")
        return take_ro((self.a, self.b))  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)

    # loop_body_list: the blunt cost of the rule above -- `xs` is bound in a
    # loop body and runs no code when destroyed, but a list is not proven
    # harmless by its type, so the members copy
    def loop_body_list(self: Own[Self]) -> int32:
        n = 0
        for i in range(2):
            xs = [5, 6]
            n += len(xs)
        return take_ro((self.a, self.b)) + n  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)

    # self_in_nested_def: a nested def may run twice, so it does not own
    # `self` and passing it on copies (a moved receiver would print 1 twice)
    def self_in_nested_def(self: Own[Self]) -> int32:
        def inner() -> int32:
            return take_self((self, 1))  # tpyc: warning(/copies H into owned storage \(argument 't' tuple element 0\)/)
        return inner() * 10 + inner()

    # loop_generator: the running generator's `finally` reads the field
    # after the return, so the members copy (a moved `a` would print 0)
    def loop_generator(self: Own[Self]) -> int32:
        for i in self.labeled_gen("loop_generator"):
            return take_ro((self.a, self.b))  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)
        return 0

    # loop_generator_single: the same with one field read
    def loop_generator_single(self: Own[Self]) -> int32:
        for i in self.labeled_gen("loop_generator_single"):
            return keep_p(self.a)  # tpyc: warning(/copies P into owned storage/)
        return 0

    # loop_free_generator: a generator over the field reads it in its
    # `finally` after the return, so the members copy
    def loop_free_generator(self: Own[Self]) -> int32:
        for i in gen_of(self.a):
            return take_ro((self.a, self.b))  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)
        return 0

    # with_self_exit: `__exit__` reads the field after the return, so the
    # members copy (a moved `a` would print 0)
    def with_self_exit(self: Own[Self]) -> int32:
        with self:
            return take_ro((self.a, self.b))  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)

    # with_self_exit_single: the same with one field read
    def with_self_exit_single(self: Own[Self]) -> int32:
        with self:
            return keep_p(self.a)  # tpyc: warning(/copies P into owned storage/)

    # async_return: an async frame outlives the return statement
    async def async_return(self: Own[Self]) -> int32:
        return take_ro((self.a, self.b))  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)

    # single_last_use: one field at the receiver's last use moves
    def single_last_use(self: Own[Self]) -> int32:
        return keep_p(self.a)  # tpyc: ok

    # bound_then_returned: the receiver's last use need not be in the return
    def bound_then_returned(self: Own[Self]) -> int32:
        n = keep_p(self.a)  # tpyc: ok
        return n

    # char_return: a returned `char` field
    def char_return(self: Own[Self]) -> char:
        return self.c  # tpyc: ok

    # enumerate_generator: the generator inside `enumerate` still runs its
    # `finally`, which reads the field, after the return
    def enumerate_generator(self: Own[Self]) -> int32:
        for i, x in enumerate(self.labeled_gen("enumerate_generator")):
            return keep_p(self.a)  # tpyc: warning(/copies P into owned storage/)
        return 0

    # zip_generator: the same inside `zip`
    def zip_generator(self: Own[Self]) -> int32:
        for x, y in zip(self.labeled_gen("zip_generator"), range(3)):
            return keep_p(self.a)  # tpyc: warning(/copies P into owned storage/)
        return 0

    # guard_local_single: `g`'s `__del__` reads the field after the return
    def guard_local_single(self: Own[Self]) -> int32:
        g = Guard(self.a, "guard_local_single")
        return keep_p(self.a)  # tpyc: warning(/copies P into owned storage/)

    # local_generator_single: `g`'s `finally` reads the field after the
    # return
    def local_generator_single(self: Own[Self]) -> int32:
        g = self.labeled_gen("local_generator_single")
        next(g, 0)
        return keep_p(self.a)  # tpyc: warning(/copies P into owned storage/)

    # match_subject: the subject's teardown (a `Guard` whose `__del__` reads
    # the field) runs after an arm's return
    def match_subject(self: Own[Self]) -> int32:
        match Guard(self.a, "match_subject"):
            case _:
                return keep_p(self.a)  # tpyc: warning(/copies P into owned storage/)

    # clause_later_read: a read `self` outlives is offered the move at the
    # last use of `self`
    def clause_later_read(self: Own[Self]) -> int32:
        n = keep_p(self.a)  # tpyc: warning(/use copy\(\) to make this explicit, or move it at the last use of `self`/)
        return n * 10 + len(self.a.xs)

    # comp_no_clause: a read in a comprehension never moves, so it is not
    # offered the move
    def comp_no_clause(self: Own[Self]) -> int32:
        t = sum([keep_p(self.a) for i in range(2)])  # tpyc: warning(/use copy\(\) to make this explicit$/)
        return t

    # match_plain_subject: an `int` subject runs nothing at its teardown, so
    # the field moves, as on master (a temporary subject that is no value
    # keeps the copy: match_subject)
    def match_plain_subject(self: Own[Self]) -> int32:
        match self.n:
            case _:
                return keep_p(self.a)  # tpyc: ok

    # clause_teardown_fails: `g`, bound after the read, holds a `Ptr` into
    # the field that its `__del__` reads, so no read of `self` may move and
    # none is offered the move
    def clause_teardown_fails(self: Own[Self]) -> int32:
        n = keep_p(self.a)  # tpyc: warning(/use copy\(\) to make this explicit$/)
        g = Guard(self.a, "clause_teardown_fails")
        return n


class HT:
    pair: tuple[P, P]

    def __init__(self) -> None:
        self.pair = (P(1), P(2))

    # tuple_after: a list local bound after the read fails the teardown
    # proof, so the tuple field is copied and warned per element, as its
    # scalar twin is
    def tuple_after(self: Own[Self]) -> int32:
        n = take_mut(self.pair)  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)
        ys = [P(3)]
        return n * 10 + len(ys)

    # tuple_before: the same with the list local bound before the read
    def tuple_before(self: Own[Self]) -> int32:
        ys = [P(3)]
        n = take_mut(self.pair)  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)
        return n * 10 + len(ys)

    # tuple_return: the whole tuple field returned past a list local
    def tuple_return(self: Own[Self]) -> Own[tuple[P, P]]:
        ys = [P(3)]
        print("tuple_return ys", len(ys))
        return self.pair  # tpyc: warning(/tuple element 0\) ?;? ?use copy\(\) to make this explicit$/) warning(/tuple element 1\) ?;? ?use copy\(\) to make this explicit$/)

    # tuple_moves: with no local the teardown could read, the whole tuple
    # field moves at the receiver's last use, as its scalar twin does
    def tuple_moves(self: Own[Self]) -> int32:
        return take_mut(self.pair)  # tpyc: ok

    # tuple_clause: a tuple field read that `self` outlives is offered the
    # move at the last use of `self`, as a scalar read is
    def tuple_clause(self: Own[Self]) -> int32:
        n = keep_t(self.pair)  # tpyc: warning(/tuple element 0\).*or move it at the last use of `self`$/)
        return n * 10 + first_len(self.pair)


def take_self(t: Own[tuple[H, int32]]) -> int32:
    h, k = t
    return len(h.a.xs) + k


class HPtr:
    a: P
    b: P
    pa: Ptr[P]

    def __init__(self) -> None:
        self.a = P(1)
        self.b = P(2)
        self.pa = self.a

    # ptr_field_in_return: `pa` aims at `a`, so moving `a` would leave `pa`
    # reading the emptied field; a pointer-typed field in the value keeps
    # every member borrowed
    def ptr_field_in_return(self: Own[Self]) -> int32:
        return take_peek((self.a, self.b), self.pa)  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)


class Base:
    a: P
    b: P

    def __init__(self) -> None:
        self.a = P(1)
        self.b = P(2)

    def count(self) -> int32:
        return len(self.a.xs)


class Sub(Base):
    def __init__(self) -> None:
        super().__init__()

    # super_in_return: `super()` reads `self` as a whole
    def super_in_return(self: Own[Self]) -> int32:
        return take_ro((self.a, self.b)) * 10 + super().count()  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)

    # super_single: `super()` reads `self` beside a single field read
    def super_single(self: Own[Self]) -> int32:
        return keep_p(self.a) * 10 + super().count()  # tpyc: warning(/copies P into owned storage/)

    # super_later: a `super()` read in a later statement keeps `self` live
    def super_later(self: Own[Self]) -> int32:
        n = keep_p(self.a)  # tpyc: warning(/use copy\(\) to make this explicit, or move it at the last use of `self`$/)
        return n * 10 + super().count()

    # super_finally: so does one in a `finally`
    def super_finally(self: Own[Self]) -> int32:
        try:
            return keep_p(self.a)  # tpyc: warning(/copies P into owned storage/)
        finally:
            print("super_finally finally", super().count())

    # super_while: so does one in a later `while` condition
    def super_while(self: Own[Self]) -> int32:
        n = keep_p(self.a)  # tpyc: warning(/use copy\(\) to make this explicit, or move it at the last use of `self`$/)
        k = 0
        while super().count() > k:
            k += 1
        return n * 10 + k

    # super_with: and one in a later `with` manager
    def super_with(self: Own[Self]) -> int32:
        n = keep_p(self.a)  # tpyc: warning(/use copy\(\) to make this explicit, or move it at the last use of `self`$/)
        with Held(super().count()) as c:
            return n * 10 + c

    # whole_super_later: `self` passed whole before a later `super()` read
    def whole_super_later(self: Own[Self]) -> int32:
        n = keep_sub(self)  # tpyc: warning(/copies Sub into owned storage/)
        return n * 10 + super().count()


class FD:
    a: P

    def __init__(self) -> None:
        self.a = P(1)

    # silent: a consuming method suppresses its receiver's finalizer
    # (BUGS.md#consuming-method-skips-receiver-del)
    def __del__(self) -> None:
        pass

    # fd_no_clause: an owner on the path (the receiver) defines `__del__`,
    # so no read of the field ever moves and none is offered the move
    def fd_no_clause(self: Own[Self]) -> int32:
        n = keep_p(self.a)  # tpyc: warning(/use copy\(\) to make this explicit$/)
        return n


class SubBox:
    s: Sub

    def __init__(self, s: Own[Sub]) -> None:
        self.s = s


def keep_sub(s: Own[Sub]) -> int32:
    b = SubBox(s)
    return len(b.s.a.xs)


async def run_async() -> None:
    h = H()
    print("async_return", await h.async_return())


def main() -> None:
    print("after", H().after())
    print("last_use", H().last_use())
    print("bare_return", H().bare_return())
    print("loop_return", H().loop_return())
    r = H().try_return()
    print("try_return", r)
    s = H().try_return_scalar()
    print("try_return_scalar", s)
    print("with_return", H().with_return())
    print("same_field_twice", H().same_field_twice())
    print("whole_self_in_return", H().whole_self_in_return())
    print("nonscalar_local_in_return", H().nonscalar_local_in_return())
    print("scalar_local_ok", H().scalar_local_ok())
    o = O()
    print("field_store", H().field_store(o))
    print("escape_copy", H().escape_copy())
    print("nested_def_return", H().nested_def_return())
    print("lambda_body", H().lambda_body())
    print("nested_def_in_return", H().nested_def_in_return())
    print("while_return", H().while_return())
    t = H().try_handler_return()
    print("try_handler_return", t)
    e = H().try_else_return()
    print("try_else_return", e)
    print("comprehension_in_return", H().comprehension_in_return())
    print("walrus_in_return", H().walrus_in_return())
    print("property_in_return", H().property_in_return())
    print("nonscalar_param_in_return", H().nonscalar_param_in_return(O()))
    print("module_var_in_return", H().module_var_in_return())
    print("scalar_kinds_ok", H().scalar_kinds_ok())
    print("scalar_field_twice_ok", H().scalar_field_twice_ok())
    print("ptr_field_in_return", HPtr().ptr_field_in_return())
    lg = H().local_generator()
    print("local_generator", lg)
    print("local_list_in_scope", H().local_list_in_scope())
    print("scalar_locals_only", H().scalar_locals_only())
    sg = H().sibling_loop_guard()
    print("sibling_loop_guard", sg)
    print("loop_body_list", H().loop_body_list())
    print("self_in_nested_def", H().self_in_nested_def())
    print("super_in_return", Sub().super_in_return())
    print("super_single", Sub().super_single())
    lgn = H().loop_generator()
    print("loop_generator", lgn)
    lgs = H().loop_generator_single()
    print("loop_generator_single", lgs)
    lfg = H().loop_free_generator()
    print("loop_free_generator", lfg)
    wse = H().with_self_exit()
    print("with_self_exit", wse)
    wss = H().with_self_exit_single()
    print("with_self_exit_single", wss)
    print("single_last_use", H().single_last_use())
    print("bound_then_returned", H().bound_then_returned())
    print("char_return", H().char_return())
    eg = H().enumerate_generator()
    print("enumerate_generator", eg)
    zg = H().zip_generator()
    print("zip_generator", zg)
    gl = H().guard_local_single()
    print("guard_local_single", gl)
    lgs1 = H().local_generator_single()
    print("local_generator_single", lgs1)
    print("super_later", Sub().super_later())
    sf = Sub().super_finally()
    print("super_finally", sf)
    print("whole_super_later", Sub().whole_super_later())
    print("super_while", Sub().super_while())
    print("super_with", Sub().super_with())
    ms = H().match_subject()
    print("match_subject", ms)
    print("clause_later_read", H().clause_later_read())
    print("comp_no_clause", H().comp_no_clause())
    mps = H().match_plain_subject()
    print("match_plain_subject", mps)
    ctf = H().clause_teardown_fails()
    print("clause_teardown_fails", ctf)
    print("tuple_after", HT().tuple_after())
    print("tuple_before", HT().tuple_before())
    a, b = HT().tuple_return()
    print("tuple_return", len(a.xs), len(b.xs))
    print("tuple_moves", HT().tuple_moves())
    print("tuple_clause", HT().tuple_clause())
    fd = FD().fd_no_clause()
    print("fd_no_clause", fd)
    asyncio.run(run_async())


main()
