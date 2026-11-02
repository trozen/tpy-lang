# A name bound anywhere in a function is local to the whole function: every
# shape here reads either that local (bound before the read) or a name the
# function never binds, so each matches CPython. The module's names stay
# untouched by the functions' locals.
import asyncio
import builtins
from builtins import len as blen, range as rng
from builtins import range as __r0
from typing import Callable, Final, Iterator
from tpy import int32, try_parse
import user_range
from user_range import Oops, Shape, size

G = 7
H = 0
K: Final[int32] = 4
c = 1

# A type alias named like a nested def below: the nested def shadows it.
type Foo = int


def helper() -> int32:
    return 1


class P:
    def __init__(self, n: int32) -> None:
        self.n = n


# Free function: the read after the local binding is the local.
def read_after() -> None:
    G = 5
    print("read_after", G)  # tpyc: ok


# Free function: bound and read on the same branch.
def branch(flag: bool) -> None:
    if flag:
        helper = 5
        print("branch", helper)  # tpyc: ok


# `global` makes the write the module's; the read sees it.
def with_global() -> None:
    global H
    H = 8
    print("global", H)  # tpyc: ok


# `nonlocal` writes the enclosing local; the enclosing read sees it.
def with_nonlocal() -> None:
    x = 9

    def bump() -> None:
        nonlocal x
        x = 6
    bump()
    print("nonlocal", x)  # tpyc: ok


# A comprehension's variable is the comprehension's own: the read after it
# is the module's G.
def comprehension_only() -> None:
    ys = [G for G in range(2)]
    print("comprehension", ys, G)  # tpyc: ok


# A walrus binds the function's local `c`; the module's `c` stays 1.
def walrus() -> None:
    v = (c := 99)
    print("walrus", v + c)  # tpyc: ok


# A parameter named like a module global is the local.
def param(G: int32) -> None:
    print("param", G)  # tpyc: ok


# An inner def's own fresh `x` is its local: mutating it leaves the
# enclosing `x` alone.
def inner_fresh_local() -> None:
    x = 1

    def inner() -> None:
        x = 6
        x += 3  # tpyc: ok
        print("inner_fresh", x)
    inner()
    print("inner_fresh", x)


# A closure reads an enclosing local bound before the def.
def closure_after_binding() -> None:
    x = 5

    def inner() -> int32:
        return x  # tpyc: ok
    print("closure", inner())


class Reader:
    # Method: a module name the method never binds.
    def read(self) -> int32:
        return helper() + G  # tpyc: ok


# Generator: a module name the generator never binds.
def gen() -> Iterator[int32]:
    yield G  # tpyc: ok
    yield helper()


# Async: a module name the coroutine never binds.
async def coro() -> int32:
    await asyncio.sleep(0)
    return G + helper()  # tpyc: ok


# A `for` target named like a module global leaks past its loop as the
# function's local, as in CPython.
def for_leak() -> None:
    for G in range(3):
        pass
    print("for_leak", G)  # tpyc: ok


# A parameter default names what it names where the function is defined:
# the module's K, though the body binds a local K.
def with_default(n: int32 = K) -> int32:
    K = n + 1
    return K


# A class pattern naming a class the function never binds.
def match_class(v: P | int32) -> None:
    match v:
        case P():  # tpyc: ok
            print("match", "P")
        case _:
            print("match", "other")


# Constructor: a module-named local bound in `__init__` is its own.
class Ctor:
    g: int32

    def __init__(self) -> None:
        G = 3
        self.g = G + 1  # tpyc: ok


class Binder:
    # Method: binds a module-named local and reads it after.
    def bind(self) -> int32:
        helper = 10
        return helper + 1  # tpyc: ok


# Generator: binds a module-named local and reads it after.
def gen_bind() -> Iterator[int32]:
    G = 20
    yield G  # tpyc: ok


# Async: binds a module-named local and reads it after.
async def coro_bind() -> int32:
    await asyncio.sleep(0)
    G = 30
    return G  # tpyc: ok


class Big:
    def __init__(self, v: int32) -> None:
        self.v = v


# A nested def whose own `except ... as` and match capture shadow enclosing
# locals: it captures neither, so the enclosing locals mutated after the def
# keep their own objects.
def nested_own_binders() -> None:
    err = Big(1)
    hit = Big(2)

    def handler(k: int32) -> int32:
        try:
            if k > 0:
                raise ValueError("x")
        except ValueError as err:  # tpyc: ok
            print("nested_binders caught")
        match k:
            case hit:  # tpyc: ok
                return hit
    err.v = 5
    hit.v = 6
    r = handler(1)
    print("nested_binders", r, err.v, hit.v)


# A module function named `len` shadows the builtin here: a range over it
# is no proof of the list's length, so the subscript keeps its check.
def len(xs: list[int32]) -> int32:
    return 3


def user_len_total(xs: list[int32]) -> int32:
    t = 0
    for i in range(len(xs)):
        t += xs[i]  # tpyc: bounds_checked(xs)
    return t


# A class operand naming the module class the function never binds.
def isinstance_class(v: P | int32) -> None:
    print("isinstance", isinstance(v, P))  # tpyc: ok


# A handler's capture read inside the handler, and a `del` after a binding.
def except_and_del() -> None:
    try:
        raise ValueError("v")
    except ValueError as e:
        print("except_inside", e)  # tpyc: ok
    x = 4
    del x  # tpyc: ok
    print("del", "done")


class Maker:
    v: int32

    def __init__(self) -> None:
        self.v = 9

    # A nested def reading the classmethod's `cls`, bound on entry.
    @classmethod
    def make(cls, o: 'Maker') -> None:
        def inner() -> int32:
            return cls().v  # tpyc: ok

        def check() -> bool:
            return isinstance(o, cls)  # tpyc: ok
        print("nested_cls", inner(), check())

    # A lambda reading the classmethod's `cls`.
    @classmethod
    def via_lambda(cls) -> None:
        mk: Callable[[], int32] = lambda: cls().v  # tpyc: ok
        print("cls_lambda", mk())

    # A lambda reading the receiver.
    def self_lambda(self) -> None:
        get: Callable[[], int32] = lambda: self.v + 1  # tpyc: ok
        print("self_lambda", get())


# A nested def named like a module type alias is the callee, not the alias.
def alias_shadow() -> None:
    def Foo() -> int:
        return 7
    print("alias_shadow", Foo())  # tpyc: ok


# A builtin type operand the module never rebinds.
def isinstance_builtin() -> None:
    print("isinstance_builtin", isinstance("x", str))  # tpyc: ok


# A nested def reading a parameter of the enclosing function.
def param_capture(n: int32) -> None:
    def inner() -> int32:
        return n + 1  # tpyc: ok
    print("param_capture", inner())


# Imported classes in the type positions: isinstance, a class pattern, an
# except clause.
def imported_types(v: Shape | int32) -> None:
    print("imported_isinstance", isinstance(v, Shape))  # tpyc: ok
    match v:
        case Shape():  # tpyc: ok
            print("imported_case", "Shape")
        case _:
            print("imported_case", "other")
    try:
        raise Oops()
    except Oops:  # tpyc: ok
        print("imported_except", "Oops")


# A loop variable rebound after its loop, before the def: the nested def
# reads that plain binding.
def rebound_after() -> None:
    for i in range(3):
        pass
    i = 5

    def inner() -> int32:
        return i + 1  # tpyc: ok
    print("rebound_after", inner())


# A name bound before the loop that rebinds it: the nested def reads the
# function's local.
def bound_before() -> None:
    i = -1
    for i in range(3):
        pass

    def inner() -> int32:
        return i + 1  # tpyc: ok
    print("bound_before", inner())


# A dotted enum operand through an imported module.
def dotted_try_parse() -> None:
    print("dotted_try_parse", try_parse(user_range.Hue, "BLUE"))  # tpyc: ok


# The builtins `range` and `len` imported under other names (this module
# shadows `len`): each call resolves to the builtin, so the loop counts and
# the range over the list's length proves the subscript in range.
def aliased_range(xs: list[int32]) -> None:
    t = 0
    for j in rng(blen(xs)):
        t += xs[j]  # tpyc: bounds_safe(xs)
    print("aliased_range", t)


# A generator expression and a comprehension over the aliased builtin: the
# frame's counted loop is the builtin itself, so a loop variable named like
# the alias, or an alias named like the frame's own bound, shadows nothing.
def aliased_range_gen() -> None:
    print("aliased_gen", list(x * 2 for x in rng(3)))  # tpyc: ok
    print("aliased_gen_shadow", list(rng for rng in rng(3)))  # tpyc: ok
    print("aliased_gen_bound", list(i for i in __r0(3)))  # tpyc: ok
    print("aliased_comp", [x + 1 for x in rng(3)])  # tpyc: ok


# The aliased builtin's counted loop in a generator body and in a method.
def aliased_range_in_gen() -> Iterator[int32]:
    for i in rng(3):  # tpyc: ok
        yield i * 10


class Summer:
    def __init__(self, base: int32) -> None:
        self.base = base

    def total(self, n: int32) -> int32:
        t = self.base
        for i in rng(n):  # tpyc: ok
            t += i
        return t


# The aliased `len` in a condition is the builtin's pure call, not a call
# that may run the closure writing the list: the fact the condition proves
# on that list survives it.
def aliased_len_cond(xs: list[int32]) -> None:
    def grow() -> None:
        xs.append(9)
    i = 0
    if i < blen(xs):
        print("aliased_len_cond", xs[i])  # tpyc: bounds_safe(xs)
    grow()
    print("aliased_len_cond grown", xs)


# The builtins module's own functions, called through the module: a counted
# loop, a counted generator, and `len` as a proving range bound.
def dotted_range(xs: list[int32]) -> None:
    n = 3
    for j in builtins.range(n):  # tpyc: ok
        print("dotted_for", j)
    print("dotted_gen", list(x * 3 for x in builtins.range(n)))  # tpyc: ok
    t = 0
    for i in range(builtins.len(xs)):
        t += xs[i]  # tpyc: bounds_safe(xs)
    print("dotted_len", t)


# A builtin re-exported by another module under a new name keeps its
# verdict: the range over its length proves the subscript in range.
def reexported_len(xs: list[int32]) -> None:
    t = 0
    for i in rng(size(xs)):
        t += xs[i]  # tpyc: bounds_safe(xs)
    print("reexported_len", t)


# The aliased `len` of dict views.
def aliased_len_views() -> None:
    d = {"a": 1, "b": 2}
    print("aliased_len_views", blen(d.values()), blen(d.keys()))  # tpyc: ok


def main() -> None:
    read_after()
    branch(True)
    with_global()
    with_nonlocal()
    comprehension_only()
    walrus()
    param(3)
    inner_fresh_local()
    closure_after_binding()
    print("method", Reader().read())
    print("generator", list(gen()))
    print("async", asyncio.run(coro()))
    for_leak()
    # The default is filled at a call in a body that binds K later.
    print("default", with_default())  # tpyc: ok
    match_class(P(1))
    print("ctor", Ctor().g)
    print("method_bind", Binder().bind())
    print("generator_bind", list(gen_bind()))
    print("async_bind", asyncio.run(coro_bind()))
    nested_own_binders()
    isinstance_class(P(1))
    except_and_del()
    # A module function named `len`: the range-bound scan keeps its check.
    print("user_len", user_len_total([1, 2, 3, 4, 5]))
    Maker.make(Maker())
    Maker.via_lambda()
    Maker().self_lambda()
    alias_shadow()
    param_capture(4)
    isinstance_builtin()
    user_range.for_over()
    user_range.comp_over()
    imported_types(Shape(2))
    rebound_after()
    bound_before()
    dotted_try_parse()
    aliased_range([1, 2, 3])
    aliased_range_gen()
    print("aliased_in_gen", list(aliased_range_in_gen()))
    print("aliased_in_method", Summer(100).total(4))
    aliased_len_cond([7])
    dotted_range([1, 2])
    reexported_len([4, 5, 6])
    aliased_len_views()
    print("user_range gen", list(user_range.gen_over()))
    print("user_range async", asyncio.run(user_range.coro_over()))
    G = 0
    K = 0
    print("module", c, G, K)


main()
print("module", G, H, K, c)
