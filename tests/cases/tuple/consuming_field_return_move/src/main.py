# A consuming method moves `self.<field>` out only inside its own `return` that
# nothing of the method runs after (no enclosing `for`/`try`/`with`, and no
# local anywhere in the method whose destruction could read the field), read
# once there, beside names proven unable to reach the field; any other field
# read is a borrow, which an owning slot copies and warns about, as in an
# ordinary method.
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


def take_ro(t: Own[tuple[P, P]]) -> int32:
    a, b = t
    return len(a.xs)


def take_mut(t: Own[tuple[P, P]]) -> int32:
    a, b = t
    a.xs.append(9)
    return len(a.xs)


def take_both(t: Own[tuple[P, P]]) -> int32:
    a, b = t
    return len(a.xs) * 10 + len(b.xs)


def take_peek(t: Own[tuple[P, P]], q: Ptr[P]) -> int32:
    a, b = t
    return len(a.xs) * 10 + q.n()


def run_twice(f: Callable[[], int32]) -> int32:
    return f() * 10 + f()


def step(g: Iterator[int32]) -> None:
    try:
        next(g)
    except StopIteration:
        pass


class Guard:
    p: Ptr[P]

    def __init__(self, p: Ptr[P]) -> None:
        self.p = p

    def __del__(self) -> None:
        print("sibling_loop_guard del", self.p.n())


GP = P(7)


class H:
    a: P
    b: P
    n: int

    def __init__(self) -> None:
        self.a = P(1)
        self.b = P(2)
        self.n = 10 ** 20

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

    # loop_return: the `for` iterator's teardown (a generator's `finally`)
    # runs after the return and could read the field
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

    # with_return: `__exit__` runs after the return
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

    # try_else_return: a return in a try's `else` is inside the `try`, so it
    # borrows (a `finally` reading the field after it is
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

    # walrus_in_return: a walrus binds a name inside the returned expression
    def walrus_in_return(self: Own[Self]) -> int32:
        return take_ro((self.a, self.b)) + (k := 4)  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)

    # property_in_return: a property reads `self` as a whole
    def property_in_return(self: Own[Self]) -> int32:
        return take_ro((self.a, self.b)) * 10 + self.first_len  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)

    # nonscalar_param_in_return: a reference-type parameter could alias the field
    def nonscalar_param_in_return(self: Own[Self], o: O) -> int32:
        return take_ro((self.a, self.b)) + len(o.p.xs)  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)

    # module_var_in_return: a module variable could hold the receiver
    def module_var_in_return(self: Own[Self]) -> int32:
        return take_ro((self.a, self.b)) + len(GP.xs)  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)

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
            gd = Guard(self.a)
        for j in range(1):
            gd = Guard(self.a)
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

    # async_return: an async frame outlives the return statement
    async def async_return(self: Own[Self]) -> int32:
        return take_ro((self.a, self.b))  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/)


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
    asyncio.run(run_async())


main()
