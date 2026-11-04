# A test whose value sema proves leaves the arm it rules out unanalyzed and
# empty, at every position that holds a test. The test stays as written:
# its decided parts that only read become literals, the rest runs as in
# CPython.
import asyncio
from typing import Callable, Iterator, Protocol
from typing import Literal, TypedDict, Unpack
from tpy import (Own, Ptr, ReturnException, dispatch, error_return, int32,
                 take_ptr)
from kin import Fin, Wing, mi_isinstance, mi_pattern, mi_union


class Animal:
    def __init__(self, name: str) -> None:
        self.name = name


class Dog(Animal):
    def bark(self) -> str:
        return "woof"


class Cat:
    lives: int32

    def __init__(self) -> None:
        self.lives = 9


class Bird:
    wings: int32

    def __init__(self, wings: int32) -> None:
        self.wings = wings


class Bat:
    span: int32

    def __init__(self) -> None:
        self.span = 3


class Griffin(Bat, Bird):
    def __init__(self) -> None:
        Bat.__init__(self)
        Bird.__init__(self, 2)


calls: list[str] = []


class Fault(Exception, ReturnException):
    pass


@error_return(Fault)
def checked(tag: str) -> Own[Dog]:
    calls.append(tag)
    return Dog(tag)


async def fetch(tag: str) -> Own[Dog]:
    calls.append(tag)
    return Dog(tag)


def probe(tag: str) -> bool:
    calls.append(tag)
    return True


def mk(tag: str) -> Own[Dog]:
    calls.append(tag)
    return Dog(tag)


# if: the dead arm calls a method only the descendant has.
def if_arm(a: Animal) -> str:
    if isinstance(a, Dog):  # tpyc: warning(/descendant type 'Dog' on 'Animal' folds to False/)
        return a.bark()  # tpyc: ok
    return "other"


# elif: the dead tail of a single-value Literal test.
def elif_arm(x: Literal["a"]) -> int32:
    if x == "b":
        return x + 1  # tpyc: ok
    elif x == "a":
        return 1
    return 2


# ternary: the dead arm has another type; the ternary is its live arm.
def ternary(a: Dog) -> str:
    w = "hello" if isinstance(a, Dog) else 42
    return w


# and / or: the right operand is dead.
def and_or(a: Dog) -> bool:
    return isinstance(a, Cat) and a.lives > 3  # tpyc: ok


# while: the body is dead, the else runs.
def while_arm(a: Dog) -> str:
    out = "w"
    while isinstance(a, Cat):
        out += str(a.lives)
    else:
        out += "-else"
    return out


# assert: the message is dead.
def assert_arm(a: Dog) -> str:  # tpyc: mir(covered)
    assert isinstance(a, Dog), a.lives
    return "asserted"


# comprehension if: the filter is decided True and goes.
def comprehension(xs: list[Dog]) -> int32:
    return len([x.name for x in xs if isinstance(x, Dog)])


# match: an arm whose class cannot match the subject's type is dead.
def match_arm(a: Dog | Cat) -> str:
    match a:
        case Bird():
            return str(a.wings)  # tpyc: ok
        case Dog():
            return "dog"
        case Cat():
            return "cat"


# match on a record subject: a descendant class pattern warns and is dead,
# an unrelated one and `case None` are dead, the arm after a covering class
# pattern is dead, and a guard decided False leaves its arm dead.
def match_record(a: Animal) -> str:
    match a:
        case Dog():  # tpyc: warning(/class pattern 'Dog' on 'Animal' never matches/)
            return a.bark()
        case Cat():
            return str(a.lives)
        case None:
            return a.lives
        case _ if False:
            return a.lives
        case Animal():
            return "animal"
        case Bird():
            return str(a.wings)


# match on an Optional subject: a class its payload is not is dead.
def match_optional(d: Dog | None) -> str:
    match d:
        case None:
            return "none"
        case Cat():
            return str(d.lives)
        case _:
            return "dog"


# match on a Literal subject: a literal outside its values is dead.
def match_literal(x: Literal["a", "b"]) -> str:
    match x:
        case "c":
            return x + 1
        case "a":
            return "A"
        case _:
            return "other"


# after a terminating arm: the rest of the block is dead.
def after_terminating(a: Dog) -> str:
    if isinstance(a, Dog):
        return a.name
    return a.lives


# a name bound only in the live arm is declared where it is bound.
def bound_in_arm(a: Dog) -> str:
    if isinstance(a, Dog):
        label = a.name + "!"
    return label


# a loop rebinds the subject: the test is not constant.
def loop_rebind() -> str:
    a: str | float = 1.5
    out = ""
    for _ in range(2):
        if isinstance(a, str):
            out += "S"
        else:
            out += "F"
        a = "s"
    return out


# try / finally: decided inside both bodies.
def try_finally(a: Dog) -> str:
    out = ""
    try:
        if isinstance(a, Cat):
            out += str(a.lives)
        out += "t"
    finally:
        if a is None:
            out += a.lives
        out += "f"
    return out


# generator: the only yield of the dead arm is gone.
def gen(a: Dog) -> Iterator[int32]:
    if isinstance(a, Cat):
        yield a.lives
    yield 7


# async: decided in a coroutine body.
async def coro(a: Dog) -> str:
    await asyncio.sleep(0)
    if isinstance(a, Cat):
        return str(a.lives)
    return "async"


class Kennel:
    def __init__(self, d: Dog) -> None:
        # A borrowed parameter stored into a field is copied (CPython
        # aliases), so the copy is warned.
        self.d = d  # tpyc: warning(/copies Dog into field/)

    # method: decided on a field subject.
    def label(self) -> str:
        if isinstance(self.d, Cat):
            return str(self.d.lives)
        return self.d.name


# a parameter mutated only in a dead arm is a const reference.
def mutate_dead(a: Dog, xs: list[int32]) -> int32:
    if isinstance(a, Cat):
        xs.append(a.lives)
    return len(xs)


# an impure decided test keeps its call.
def impure(tag: str) -> str:
    if isinstance(mk(tag), Cat):
        return "cat"
    return "kept"


# an impure decided test that propagates an @error_return error keeps it.
@error_return(Fault)
def impure_checked(tag: str) -> str:
    if isinstance(checked(tag), Cat):
        return "cat"
    return "checked"


# an impure decided test that awaits keeps the await.
async def impure_await(tag: str) -> str:
    if isinstance(await fetch(tag), Cat):
        return "cat"
    return "awaited"


# a union non-member is decided False, the arm is dead (was an error).
def non_member(v: Dog | Cat) -> str:
    if isinstance(v, Bird):
        return str(v.wings)
    if isinstance(v, (Bird, Bat)):
        return "bb"
    return "member"


# a ternary whose dead arm has another type (was an error).
def ternary_types() -> str:
    x = "hello" if True else 42
    return x


# a Literal[True] truth test is decided.
def literal_truth(x: Literal[True]) -> str:
    if x:
        return "truthy"
    return 1


# a while head over a Literal parameter narrows its body (was refused).
def while_literal(mode: Literal["r", "w"]) -> int32:
    n = 0
    while mode == "r":
        n += 1
        if n > 2:
            break
    return n


# membership under a match arm's Literal narrowing (was refused).
def match_membership(mode: Literal["r", "w", "a"]) -> str:
    match mode:
        case "r" | "w":
            if mode in ("r", "w"):
                return "rw"
            return 0
        case "a":
            return "append"


# Python equality: Literal[1] == True.
def literal_true(x: Literal[1]) -> str:
    if x == True:
        return "eq"
    return "ne"


# a local bound once to a literal decides the test.
def local_constant() -> int32:
    debug = False
    if debug:
        return "x" + 1
    return 5


# a non-Optional is never None.
def never_none(a: Dog) -> str:
    if a is None:
        return a.lives
    return "some"


# multiple inheritance: a class deriving both keeps the arm visible.
def multi(a: Bat) -> str:
    if isinstance(a, Bird):  # tpyc: warning(/'Griffin' derives both/)
        return "bird"
    return "bat"


# value ranges decide no arm: a `for` body's write leaves `t` unknown.
def neg(x: int32) -> int32:
    return x - 100


def range_loop(xs: list[int32]) -> str:
    t = 0
    for x in xs:
        t += neg(x)
    if t < 0:  # tpyc: ok
        return "negative"
    return "non-negative"


# value ranges decide no arm: the walrus writes `x` inside the test.
def range_walrus() -> str:
    x = 0
    if x == 0 and (x := 1) == 1:  # tpyc: ok
        return "live " + str(x)
    return "dead"


# a comprehension's own `x` is another variable than the constant `x`.
def comp_shadow() -> int32:
    x = True
    return len([x for x in [False, True] if x])  # tpyc: ok


shadowed = 5


# a nested def's own constant local decides nothing in the outer body.
def nested_local() -> str:
    def inner() -> int32:
        shadowed = 7
        return shadowed
    inner()
    if shadowed == 7:  # tpyc: ok
        return "wrong"
    return "right"


# not a constant: a closure rebinds it through `nonlocal`.
def nonlocal_rebind() -> str:
    n = 0

    def bump() -> None:
        nonlocal n
        n = 5
    bump()
    if n == 0:  # tpyc: ok
        return "wrong"
    return "right"


def setp(p: Ptr[int32]) -> None:
    pass


# not a constant: written twice.
def second_write(k: bool) -> str:
    d = False
    if k:
        d = True
    if d:  # tpyc: ok
        return "right"
    return "wrong"


# an address taken: the range proven for `x` no longer holds.
def ptr_range() -> int32:
    x: int32 = 2
    setp(take_ptr(x))
    return 10 // x  # tpyc: div_checked(x)


class Truth:
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def __bool__(self) -> bool:
        calls.append(self.tag)
        return True


# a user `__bool__` runs: the decided `and` / `or` keeps its test as written.
def user_bool(t: Truth) -> str:
    if t and False:  # tpyc: ok
        return a_name_that_is_missing
    if t or True:
        return "or"
    return "unreached"


class AnyEq:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __eq__(self, other: int32) -> bool:
        return True


# a user `__eq__` may answer anything: no contradiction is proven.
def user_eq(x: AnyEq) -> str:
    if x == 1 and x == 2:  # tpyc: ok
        return "live"
    return "dead"


# a Literal compare at a value position is its bool value.
def value_compare(mode: Literal["rb"], m: Literal["r", "w"]) -> str:
    b = mode == "rb"  # tpyc: ok
    return str(b) + " " + str(m == "r" or m == "w")


# a match guard that runs code and is decided False: the guard runs, the
# arm never does, and its body is not checked.
def guard_impure(x: bool) -> str:
    out = "live"
    match x:
        case True if isinstance(mk("guard"), Cat):
            out = a_name_that_is_missing  # tpyc: ok
        case _:
            pass
    return out


# an Optional match covered by `None` and a class: the later arm is dead.
def match_covered(d: Dog | None) -> str:
    match d:
        case None:
            return "none"
        case Dog():
            return "dog"
        case _:
            return d.lives  # tpyc: ok


# a match on a `None` subject runs the one arm that matches it.
def match_none(x: None) -> str:
    match x:
        case Bird():
            return x.wings  # tpyc: ok
        case None:
            return "none"
        case _:
            return 1


def nothing() -> None:
    calls.append("nothing")


# `match` on a call returning None: the call runs, `case 1` is dead and
# `case None` is the arm left.
def match_none_call() -> str:
    match nothing():
        case 1:
            return a_name_that_is_missing  # tpyc: ok
        case None:
            return "none"


# a dead class pattern with a keyword sub-pattern.
def keyword_pattern(a: Dog | Cat) -> str:
    match a:
        case Bird(wings=0):
            return a.wings  # tpyc: ok
        case _:
            return "kw"


# a loop rebinds the Optional and the Literal-narrowed local: the tests vary.
def loop_rebind_narrowed() -> str:
    o: Dog | None = None
    m: Literal["a", "b"] = "a"
    out = ""
    for _ in range(2):
        if o is None:
            out += "N"
        else:
            out += "D"
        if m == "a":
            out += "a"
        else:
            out += "b"
        o = Dog("l")
        m = "b"
    return out


# a decided ternary in a comprehension element.
def comp_ternary(xs: list[Dog]) -> str:
    return " ".join([x.name if isinstance(x, Dog) else x.lives for x in xs])  # tpyc: ok


class Ctor:
    v: int32

    # constructor: decided in `__init__`.
    def __init__(self, a: Dog) -> None:
        if isinstance(a, Cat):
            self.v = a.lives  # tpyc: ok
        self.v = 1


# closure: decided in a nested def and in a lambda.
def closure(a: Dog) -> str:
    def inner() -> str:
        if isinstance(a, Cat):
            return a.lives  # tpyc: ok
        return "inner"
    f: Callable[[], str] = lambda: "lam" if isinstance(a, Dog) else a.lives  # tpyc: ok
    return inner() + " " + f()


class Guard:
    def __enter__(self) -> "Guard":
        return self

    def __exit__(self, kind: None, value: None, tb: None) -> None:
        pass


# with body: decided inside the block.
def with_body(a: Dog) -> str:
    with Guard():
        if isinstance(a, Cat):
            return a.lives  # tpyc: ok
        return "with"


# Python equality: Literal[0] == False.
def literal_zero(x: Literal[0]) -> str:
    if x == False:  # tpyc: ok
        return "eq"
    return 1


# `is not None` on a non-Optional is True.
def not_none(a: Dog) -> str:
    if a is not None:
        return "some"
    return a.lives  # tpyc: ok


# Both callees only read their owned argument, so the Own is warned
# unconsumed; the sections that call them pin the move at the call.
def take_list(x: Own[list[int32]]) -> int32:  # tpyc: warning(/never consumed/)
    return len(x)


def take_dog(d: Own[Dog]) -> str:  # tpyc: warning(/never consumed/)
    return d.name


# Own argument: a decided ternary or `or` hands the slot its live name,
# moved at its last use as `take_list(xs)` is -- no copy.
def own_arg() -> str:
    xs = [1, 2]
    k = True
    a = take_list(xs if k else [3])  # tpyc: ok
    ys = [4]
    m = False
    b = take_list(m or ys)  # tpyc: ok
    d = Dog("own")
    other = Dog("other")
    flag = True
    return f"{a} {b} {take_dog(d if flag else other)}"  # tpyc: ok


# alias binding: the new name binds the live list itself, so a write
# through it shows on the old name, as in CPython.
def alias_binding() -> str:
    xs = [1, 2]
    k = True
    ys = xs if k else [3]  # tpyc: ok
    ys.append(9)
    m = False
    zs = m or xs  # tpyc: ok
    zs.append(7)
    return str(xs)


def grow(xs: list[int32]) -> Iterator[int32]:
    xs.append(5)
    yield len(xs)


async def agrow(xs: list[int32]) -> int32:
    xs.append(6)
    return len(xs)


# generator frame argument: the frame borrows the live list.
def frame_arg() -> str:
    xs = [1, 2]
    k = True
    n = list(grow(xs if k else [3]))  # tpyc: ok
    return f"{n} {xs}"


# async frame argument: the coroutine borrows the live list.
async def async_frame_arg() -> str:
    xs = [1, 2]
    k = True
    n = await agrow(xs if k else [3])  # tpyc: ok
    return f"{n} {xs}"


class Adder(Protocol):
    def add(self, v: int32) -> None: ...


class Bag:
    items: list[int32]

    def __init__(self) -> None:
        self.items = []

    def add(self, v: int32) -> None:
        self.items.append(v)


def push(p: Adder) -> None:
    p.add(4)


# protocol parameter: the argument is the live record, not a copy.
def protocol_arg() -> str:
    b = Bag()
    k = True
    push(b if k else Bag())  # tpyc: ok
    push(Bag() if not k else b)  # tpyc: ok
    return str(b.items)


class Gate:
    tag: str
    val: bool

    def __init__(self, tag: str, val: bool) -> None:
        self.tag = tag
        self.val = val

    def __bool__(self) -> bool:
        calls.append(self.tag)
        return self.val


# A decided test whose operands run user code stays as written: each
# operand's truth test runs once, as CPython runs it.
def kept_and(t: Gate, u: Gate) -> str:
    if (t and u) and False:  # tpyc: ok
        return a_name_that_is_missing
    return "F"


def kept_ternary(t: Gate, u: Gate, k: bool) -> str:
    if (t if k else u) and False:  # tpyc: ok
        return a_name_that_is_missing
    return "F"


def kept_or(t: Gate, u: Gate) -> str:
    if (t or u) or True:  # tpyc: ok
        return "T"
    return a_name_that_is_missing


def kept_while(t: Gate, u: Gate) -> str:
    while (t or u) and False:  # tpyc: ok
        return a_name_that_is_missing
    return "W"


def kept_not(t: Gate, u: Gate) -> str:
    if not (t and u) and False:  # tpyc: ok
        return a_name_that_is_missing
    assert (t or u) or True  # tpyc: ok
    return "N"


class Pouch:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __len__(self) -> int32:
        calls.append("len")
        return self.n


# a user `__len__` is the truth test the kept operand runs.
def kept_len(p: Pouch) -> str:
    if p and False:  # tpyc: ok
        return a_name_that_is_missing
    return "L"


# a decided ternary inside a decided ternary, and a decided `or` inside
# one: the slot takes the innermost live part, the list itself.
def nested_decided() -> str:
    xs = [1]
    ys = [2]
    z = (xs if True else ys) if True else ys  # tpyc: ok
    z.append(3)
    e = ""
    s = "s"
    t = (e or s) if True else "y"  # tpyc: ok
    return f"{xs} {ys} {t}"


class Opt(TypedDict):
    n: int32


def kw_sink(**kw: Unpack[Opt]) -> str:
    return str(kw["n"])


@dispatch
def over(xs: list[int32]) -> str:
    xs.append(9)
    return "list"


@dispatch
def over(xs: str) -> str:
    return "str"


# builtin constructor argument: the live list, read in place.
def builtin_ctor() -> str:
    xs = [1]
    ys = [2]
    k = True
    zs = list(xs if k else ys)  # tpyc: ok
    zs.append(3)
    return f"{xs} {zs} {str(xs if k else ys)}"


# TypedDict construction from keywords: the field takes the live int32.
def typed_dict() -> str:
    k = True
    return kw_sink(n=7 if k else 10**30)  # tpyc: ok


# overloaded callee: the variant receives the live list itself.
def overloaded() -> str:
    xs = [1]
    ys = [2]
    k = True
    r = over(xs if k else ys)  # tpyc: ok
    return f"{r} {xs}"


class Holder:
    x: Dog

    def __init__(self) -> None:
        self.x = Dog("h")

    # a plain field read is pure, so the test folds: `self.x is None` on a
    # never-None field is its literal.
    def field_none(self) -> str:
        if self.x is None:  # tpyc: ok
            return self.x.lives
        return "some"

    # a plain field read is pure, so the ternary's test folds and the
    # ternary is its live arm, of that arm's type.
    def field_ternary(self) -> str:
        v = 1 if isinstance(self.x, Cat) else "live"  # tpyc: ok
        return v


# a coverage proof over two Literal compares folds as one test.
def coverage(x: Literal["a", "b"]) -> str:
    if x == "a" or x == "b":  # tpyc: ok
        return "covered"
    return x + 1


# the right operand of a decided `and` never runs: it is not analyzed.
def false_and() -> str:
    if False and a_name_that_is_missing():  # tpyc: ok
        return "dead"
    return "skipped"


# a call beside a decided leaf runs; the leaf is its literal.
def call_and_none(a: Dog) -> str:
    if probe("ping") and a is None:  # tpyc: ok
        return a.lives
    return "pinged"


# the truth test of a call's result runs: `[]` is falsy in CPython too.
def nums(tag: str) -> Own[list[int32]]:
    calls.append(tag)
    return []


def builtin_truth() -> str:
    if nums("g") and False:  # tpyc: ok
        return a_name_that_is_missing
    return "g"


# an isinstance argument that runs code is evaluated, its type's
# `__bool__` is not.
def make_truth(tag: str) -> Own[Truth]:
    calls.append("make " + tag)
    return Truth(tag)


def impure_isinstance() -> str:
    if isinstance(make_truth("m"), Cat):
        return a_name_that_is_missing  # tpyc: ok
    return "made"


# an `and` value whose left operand is undecided keeps both operands.
def value_undecided(t: bool) -> str:
    v = t and False  # tpyc: ok
    return str(v)


# a name bound in the live arm of a decided `if` is assigned after it.
def live_binding() -> str:
    if True:
        x = 3
    return str(x)  # tpyc: ok


# a name bound only in a dead arm is still a local: the live code binds it
# before reading it, so the read is the local, not the global `shadowed`.
def dead_local() -> str:
    if False:
        shadowed = "dead"
    shadowed = 9
    return str(shadowed)  # tpyc: ok


# under `global`, the dead arm's write is the global's: the read is too.
def dead_global() -> str:
    global shadowed
    if False:
        shadowed = 3
    return str(shadowed)  # tpyc: ok


# a walrus inside a lambda binds the lambda's own name, so the dead arm makes
# no local of the outer function: the read is the global `shadowed`.
def lambda_walrus_global() -> str:
    if False:
        cb: Callable[[], int32] = lambda: (shadowed := 3)  # tpyc: ok
    return str(shadowed)  # tpyc: ok


# a closure's read of an enclosing local is that local, bound before it.
def closure_local() -> str:
    shadowed = 3
    cb: Callable[[], int32] = lambda: shadowed  # tpyc: ok
    def bump() -> int32:
        return shadowed + 1  # tpyc: ok
    return str(cb()) + " " + str(bump())


# the decided edge reaches MIR: nothing falls off the end after the `if`.
def decided_return(t: bool) -> int32:  # tpyc: mir(covered)
    if t or True:
        return 1


# a generator whose only yield is dead yields nothing.
def gen_dead_only() -> Iterator[int32]:
    if False:
        yield a_name_that_is_missing()  # tpyc: ok


# a coroutine whose only await is dead runs to its return.
async def coro_dead_only() -> str:
    if False:
        await a_name_that_is_missing()  # tpyc: ok
    return "coro"


# a walrus in a decided test binds as written.
def walrus_scalar() -> str:
    t = 1
    if (w := t) and False:  # tpyc: ok
        return "dead"
    return str(w)


def either(k: bool) -> Own[Dog | Cat]:
    calls.append("either")
    if k:
        return Dog("e")
    return Cat()


# an isinstance over a call returning a union without the class: the call
# runs, the arm is dead.
def non_member_call() -> str:
    if isinstance(either(True), int):
        return a_name_that_is_missing  # tpyc: ok
    return "ran"


# a None subject: an or-pattern whose alternatives are all dead is dead,
# and a guard that folds to True is no guard.
def match_none_or(x: None) -> str:
    match x:
        case 1 | 2:
            return a_name_that_is_missing  # tpyc: ok
        case None:
            return "or"


def match_none_guard(x: None) -> str:
    match x:
        case None if True:
            return "guard"


# a None subject with no arm left runs none (and warns, as any match
# leaving a value uncovered does).
def match_none_empty(x: None) -> str:
    match x:  # tpyc: warning(/non-exhaustive match on 'None'/)
        case 1:
            return a_name_that_is_missing  # tpyc: ok
    return "empty"


# a decided Own argument: the live name moves at its last use, as
# `take_list(xs)` does, and a later read copies it, warned as there.
def own_read_after() -> str:
    xs = [1, 2]
    k = True
    n = take_list(xs if k else [3])  # tpyc: warning(/copies list\[int32\] into owned storage/)
    return f"{n} {len(xs)}"


# an elif whose test runs code and is decided False, after an undecided
# head: the call runs, the arm is empty.
def elif_impure(k: bool, a: Dog) -> str:
    if k:
        return "head"
    elif probe("e") and a is None:  # tpyc: ok
        return a.lives
    return "after"


# a head decided False over an undecided elif chain: the chain runs.
def false_head_chain(k: int32, a: Dog) -> str:
    if a is None:  # tpyc: ok
        return a.lives
    elif k > 1:
        return "big"
    elif k > 0:
        return "small"
    return "none"


# a walrus target bound by a decided test is read in its live arm.
def count(tag: str) -> int32:
    calls.append(tag)
    return 4


def walrus_live() -> int32:
    if (n := count("n")) or True:
        return n  # tpyc: ok


# a comprehension filter decided True whose call runs: every `mk` runs.
def comp_impure(xs: list[str]) -> int32:
    return len([x for x in xs if isinstance(mk(x), Dog)])  # tpyc: ok


# a decided `while` whose head runs code: MIR takes only the decided edge,
# so `v` is assigned at the read.
def tick(n: int32) -> bool:
    return n > 5


def while_impure(n: int32) -> int32:  # tpyc: mir(covered)
    while tick(n) or True:
        v = n + 1
        break
    return v


# a `while` head decided True that runs code, with only a `return` in the
# body: nothing follows the loop, and the head still runs (user `__bool__`
# here, a plain call below).
def while_true_return(t: Gate) -> str:
    while t or True:  # tpyc: ok
        return "W"


def while_true_call() -> int32:
    while count("wc") > 9 or True:  # tpyc: ok
        return 1


# the head of a decided-True `while` runs before every pass, after a
# `continue` too.
def while_true_passes() -> int32:
    i = 0
    while count("wp") > 9 or True:  # tpyc: ok
        i += 1
        if i == 3:
            break
        continue
    return i


# a generator and a coroutine whose decided impure test guards the yield /
# the await: the test runs, the arm is empty.
def gen_impure(a: Dog) -> Iterator[int32]:
    if probe("gy") and a is None:  # tpyc: ok
        yield a.lives
    yield 1


async def coro_impure(a: Dog) -> str:
    if probe("ca") and a is None:  # tpyc: ok
        await a_name_that_is_missing()
    await asyncio.sleep(0)
    return "coro"


# a dead `while` body's binding is a local: the live code binds it first.
def while_dead_local(a: Dog) -> str:
    while a is None:  # tpyc: ok
        spare = a.lives
    spare = "s"
    return spare


# a dead match arm's capture is a local: the live code binds it first.
def dead_capture_after(a: Dog) -> str:
    match a:
        case Bird(wings=w):  # tpyc: ok
            return str(w)
        case _:
            pass
    w = "rebound"
    return w


# a guard decided False keeps its case: the capture binds before the guard
# fails, as in CPython; a case that binds nothing goes.
def guard_capture(n: int32) -> str:
    x = 0
    match n:
        case x if False:  # tpyc: ok
            return "dead"
        case 1 if False:
            return "dead"
        case _:
            pass
    return str(x)


# a guard that folds to True is no guard: the arms after it are dead.
def guard_true(n: int32) -> str:
    match n:
        case x if True:  # tpyc: ok
            return str(x)
        case _:
            return a_name_that_is_missing  # tpyc: ok


# a decided `if` declares in front of it only what is read after it.
def arm_local() -> str:
    if True:
        data = [1]
        data.append(2)
        x = len(data)
    return str(x)  # tpyc: ok


# `global` in a dead arm declares the name for the whole function: the
# live write is the global's.
def dead_global_decl() -> str:
    if False:
        global shadowed
    shadowed = 7  # tpyc: ok
    return str(shadowed)


# `nonlocal` in a dead arm declares the name for the whole nested def: its
# write is the enclosing function's variable.
def dead_nonlocal_decl() -> str:
    n = 5

    def inner() -> None:
        if False:
            nonlocal n
        n = 7  # tpyc: ok

    inner()
    return str(n)


# a def nested in a dead arm of a nested def declares for its own scope:
# the enclosing nested def's `x` stays its own local.
def inner_scope_nonlocal() -> str:
    def inner() -> str:
        x = 2
        if False:
            def dead() -> None:
                nonlocal x
                x = 9
        return str(x)  # tpyc: ok

    return inner()


# `global` in a dead match arm (a class pattern, a guard decided False)
# declares the name for the whole function, as in a dead `if` arm.
def dead_match_global(a: Dog) -> str:
    match a:
        case Cat():
            global shadowed
        case Dog() if False:
            global shadowed
        case _:
            pass
    shadowed = 9  # tpyc: ok
    return str(shadowed)


# constructor: a `global` after the field init (here in a dead arm) runs
# nothing, so the field still initializes in the member init list.
class DeclCtor:
    def __init__(self, n: int32) -> None:
        self.n = n
        if False:
            global shadowed
        shadowed = n  # tpyc: ok


# a match with no arm left is what its subject runs: nothing for a name,
# the call for a call.
def match_no_arm(a: Dog) -> str:
    match a:  # tpyc: warning(/non-exhaustive match on 'Dog'/)
        case Cat():
            return "cat"
    match mk("no_arm"):  # tpyc: warning(/non-exhaustive match on 'Dog'/)
        case Cat():
            return "cat"
    return "none"


class Tag:
    def __init__(self, n: int32, pet: Own[Bird]) -> None:
        self.n = n
        self.pet = pet


# a match with no arm left over a field read through an Optional keeps the
# read: its None check raises as in CPython.
def no_arm_checked(o: Tag | None) -> str:
    match o.n:  # tpyc: warning(/Potential None access/) warning(/non-exhaustive match/)
        case 7 if False:
            return "x"
    return "after"


# an `isinstance` over a field read through an Optional is kept, not folded:
# its None check raises as in CPython.
def fold_checked(o: Tag | None) -> str:
    if isinstance(o.pet, Cat):  # tpyc: warning(/Potential None access/)
        return "x"
    return "after"


# a `del` after a decided `if` needs the live arm's binding there, as a
# read does.
def del_after() -> str:
    if True:
        x = [5]
    del x  # tpyc: ok
    return "deleted"


# a `del` a later binding reaches first does not need the arm's binding: the
# decided arm's `x` stays a stack array inside the arm.
def del_rebind() -> str:
    if True:
        x = [1]
        n = len(x)
    x = [2]
    del x  # tpyc: ok
    return str(n)


# a walrus that may not run leaves the `del` after it needing the decided
# arm's binding.
def del_cond_walrus(flag: bool) -> str:
    if True:
        x = False
    flag and (x := True)
    del x  # tpyc: ok
    if True:
        y = 1
    z = (y := 2) if flag else 0
    del y  # tpyc: ok
    return str(z)


# a `finally` body is walked twice: what the decided arm binds is declared in
# front of it for the read and the `del` after it.
def finally_arm() -> str:
    try:
        pass
    finally:
        if True:
            y = [3]
        n = len(y)  # tpyc: ok
        del y  # tpyc: ok
    return str(n)


def take_str(s: Own[str]) -> int32:
    return len(s)


# a match capture binds its name at the arm's entry: the arm's read of the
# capture keeps no earlier `x` live, so the call takes `x` by move.
def capture_rebind(a: Animal, s: str) -> str:
    x = s + "!"
    out = str(take_str(x))  # tpyc: ok
    match a:
        case Animal(name=x):
            out = out + " " + x
    return out


# a ternary's arms are values: a decided `or` there is not folded to a
# literal, the arm keeps its string value.
def ternary_arm_value(k: bool, y: str) -> str:
    x = "yes"
    if (x or "") if k else y:  # tpyc: ok
        return "yes"
    return "no"


def none_effect() -> None:
    calls.append("effect")


# a call returning None as the subject runs once, before its arm.
def none_call_once() -> str:
    match none_effect():  # tpyc: ok
        case None:
            calls.append("arm")
    return "after"


# `case None:` matches every value of a None subject: the arms after it
# are dead.
def none_then_dead(x: None) -> str:
    match x:  # tpyc: ok
        case None:
            out = "none"
        case 1:
            out = a_name_that_is_missing  # tpyc: ok
        case _:
            out = a_name_that_is_missing  # tpyc: ok
    return out


# a None subject with no arm left still runs its call.
def none_no_arm() -> str:
    match none_effect():  # tpyc: warning(/non-exhaustive match on 'None'/)
        case 1:
            return a_name_that_is_missing  # tpyc: ok
    return "after"


# a `del` in the arm a None subject runs is analyzed once.
def none_del(x: None) -> str:
    d = Dog("del")
    match x:
        case None:
            del d  # tpyc: ok
            return "deleted"
    return "kept"


# a generator whose None-subject arm suspends: the call runs once.
def none_gen() -> Iterator[int32]:
    match none_effect():  # tpyc: ok
        case None:
            yield 1
            yield 2


# a None subject with a guard whose value varies: the guard runs, and a
# failed one falls to `case _:` -- by name and over a call returning None.
def none_guard(x: None, k: bool) -> str:
    match x:  # tpyc: ok
        case None if k:
            return "none"
        case _:
            return "other"


def none_guard_call(k: bool) -> str:
    match none_effect():  # tpyc: ok
        case None if probe("guard") and k:
            out = "none"
        case _:
            out = "other"
    return out


# a None subject that is a `print(...)` call: the call runs once as the
# statement it is, then the arm; with no arm left it is all that runs.
def none_print() -> str:
    match print("none_print", "subject"):  # tpyc: ok
        case None:
            return "arm"


def none_print_no_arm() -> str:
    match print("none_print_no_arm", "subject"):  # tpyc: warning(/non-exhaustive match on 'None'/)
        case 1:
            return a_name_that_is_missing  # tpyc: ok
    return "after"


@error_return(Fault)
def none_chk(t: str) -> None:
    calls.append(t)
    if t == "bad":
        raise Fault()


# a None subject that is an @error_return call: its error propagates, as
# the call's statement form does; and with no arm left.
@error_return(Fault)
def none_er(t: str) -> str:
    match none_chk(t):  # tpyc: ok
        case None:
            return "ok"


@error_return(Fault)
def none_er_no_arm(t: str) -> str:
    match none_chk(t):  # tpyc: warning(/non-exhaustive match on 'None'/)
        case 1:
            return a_name_that_is_missing  # tpyc: ok
    return "after"


async def anone() -> None:
    await asyncio.sleep(0)
    calls.append("awaited")


# a None subject that is an `await`: the frame suspends on it once, ahead
# of the match; and with no arm left.
async def none_await() -> str:
    match await anone():  # tpyc: ok
        case None:
            return "aw"


async def none_await_no_arm() -> str:
    match await anone():  # tpyc: warning(/non-exhaustive match on 'None'/)
        case 1:
            return a_name_that_is_missing  # tpyc: ok
    return "after"


# an awaited None subject runs once, before its arm, when the arm itself
# awaits (the match's arms become frame states).
async def none_await_arm_awaits() -> str:
    match await anone():  # tpyc: ok
        case None:
            calls.append("arm")
            await asyncio.sleep(0)
            calls.append("arm2")
    return "aa"


# an awaited None subject in a loop whose arm continues / breaks out of it.
async def none_await_arm_jumps() -> str:
    i = 0
    while i < 3:
        i += 1
        match await anone():  # tpyc: ok
            case None:
                calls.append("arm" + str(i))
                if i == 1:
                    continue
                break
    return "aj"


# an awaited None subject inside an `if` branch of the coroutine.
async def none_await_in_if(k: bool) -> str:
    if k:
        match await anone():  # tpyc: ok
            case None:
                calls.append("arm")
        return "if"
    return "else"


# a walrus subject whose only arm is dead still binds its target.
def walrus_subject() -> int32:
    n = 0
    match (n := 4):  # tpyc: warning(/non-exhaustive match on 'int32'/)
        case _ if False:
            print("walrus_subject", "dead")
    return n


# a walrus expression statement binds its target.
def walrus_stmt() -> int32:
    m = 0
    (m := 5)  # tpyc: ok
    return m


# a match that runs its one arm is decided: what follows a returning arm
# is dropped unanalyzed, as after a decided `if`.
def none_tail(x: None) -> str:
    match x:  # tpyc: ok
        case None:
            return "none"
    return a_name_that_is_missing  # tpyc: ok


class NoneHolder:
    f: None

    def __init__(self) -> None:
        self.f = None


# a guarded match on a None-typed field: the field read binds nothing.
def none_field(h: NoneHolder, k: bool) -> str:
    match h.f:  # tpyc: ok
        case None if k:
            return "fnone"
        case _:
            return "other"


# a guard decided False that runs code, before an arm that returns: the
# guard runs, the next arm follows, and nothing falls off the end -- a
# guarded chain, a union switch, an int32 switch (after a live guard of
# the same label) and a str switch.
def guard_dead_chain(x: int) -> str:
    match x:
        case 1 if isinstance(mk("chain"), Cat):  # tpyc: ok
            return "dead"
        case 1:
            return "one"
        case _:
            return "other"


def guard_dead_union(x: int | str) -> str:
    match x:
        case int() if isinstance(mk("union"), Cat):  # tpyc: ok
            return "dead"
        case int():
            return "int"
        case str():
            return "str"


def guard_dead_switch(x: int32, k: bool) -> str:
    match x:
        case 1 if k:
            return "k"
        case 1 if isinstance(mk("switch"), Cat):  # tpyc: ok
            return "dead"
        case 1:
            return "one"
        case _:
            return "other"


def guard_dead_str(s: str) -> str:
    match s:
        case "a" if isinstance(mk("str"), Cat):  # tpyc: ok
            return "dead"
        case "a":
            return "A"
        case "bb":
            return "B"
        case "ccc":
            return "C"
        case "dddd":
            return "D"
        case "eeeee":
            return "E"
        case _:
            return "other"


def main() -> None:
    print("if", if_arm(Animal("d")))
    print("elif", elif_arm("a"))
    print("ternary", ternary(Dog("t")))
    print("and_or", and_or(Dog("x")))
    print("while", while_arm(Dog("w")))
    print("assert", assert_arm(Dog("a")))
    print("comprehension", comprehension([Dog("c"), Dog("e")]))
    print("match", match_arm(Dog("m")), match_arm(Cat()))
    print("match_record", match_record(Animal("m")))
    print("match_optional", match_optional(None), match_optional(Dog("o")))
    print("match_literal", match_literal("a"), match_literal("b"))
    print("after", after_terminating(Dog("r")))
    print("bound", bound_in_arm(Dog("b")))
    print("loop", loop_rebind())
    print("try", try_finally(Dog("y")))
    print("gen", list(gen(Dog("g"))))
    print("async", asyncio.run(coro(Dog("z"))))
    print("method", Kennel(Dog("k")).label())
    xs = [1, 2]
    n = mutate_dead(Dog("q"), xs)
    xs.append(3)
    print("mutate", n, xs)
    print("impure", impure("i"), calls)
    try:
        print("impure_checked", impure_checked("c"), calls)
    except Fault:
        print("impure_checked fault")
    print("impure_await", asyncio.run(impure_await("w")), calls)
    print("non_member", non_member(Dog("v")))
    print("ternary_types", ternary_types())
    print("literal_truth", literal_truth(True))
    print("while_literal", while_literal("r"), while_literal("w"))
    print("match_membership", match_membership("w"), match_membership("a"))
    print("literal", literal_true(1))
    print("local", local_constant())
    print("none", never_none(Dog("n")))
    print("multi", multi(Bat()))
    print("range_loop", range_loop([1, 2]))
    print("range_walrus", range_walrus())
    print("comp_shadow", comp_shadow())
    print("nested_local", nested_local())
    print("nonlocal", nonlocal_rebind())
    print("second", second_write(True))
    print("ptr_range", ptr_range())
    calls.clear()
    print("user_bool", user_bool(Truth("b")), calls)
    print("user_eq", user_eq(AnyEq()))
    print("value", value_compare("rb", "w"))
    calls.clear()
    print("guard_impure", guard_impure(True), calls)
    print("match_covered", match_covered(None), match_covered(Dog("c")))
    print("match_none", match_none(None))
    calls.clear()
    print("match_none_call", match_none_call(), calls)
    print("keyword", keyword_pattern(Cat()))
    print("loop_narrowed", loop_rebind_narrowed())
    print("comp_ternary", comp_ternary([Dog("p"), Dog("q")]))
    print("ctor", Ctor(Dog("c")).v)
    print("closure", closure(Dog("z")))
    print("with", with_body(Dog("w")))
    print("literal_zero", literal_zero(0))
    print("not_none", not_none(Dog("s")))
    print("own_arg", own_arg())
    print("alias_binding", alias_binding())
    print("frame_arg", frame_arg())
    print("async_frame_arg", asyncio.run(async_frame_arg()))
    print("protocol_arg", protocol_arg())
    calls.clear()
    print("kept_and", kept_and(Gate("a1", True), Gate("a2", True)), calls)
    calls.clear()
    print("kept_ternary", kept_ternary(Gate("t1", True), Gate("t2", True),
                                       True), calls)
    calls.clear()
    print("kept_or", kept_or(Gate("o1", False), Gate("o2", False)), calls)
    calls.clear()
    print("kept_while", kept_while(Gate("w1", False), Gate("w2", False)),
          calls)
    calls.clear()
    print("kept_not", kept_not(Gate("n1", True), Gate("n2", False)), calls)
    calls.clear()
    print("kept_len", kept_len(Pouch()), calls)
    print("nested_decided", nested_decided())
    print("builtin_ctor", builtin_ctor())
    print("typed_dict", typed_dict())
    print("overloaded", overloaded())
    h = Holder()
    print("field_none", h.field_none(), h.field_ternary())
    print("coverage", coverage("b"))
    print("false_and", false_and())
    calls.clear()
    print("call_and_none", call_and_none(Dog("p")), calls)
    calls.clear()
    print("builtin_truth", builtin_truth(), calls)
    calls.clear()
    print("impure_isinstance", impure_isinstance(), calls)
    print("value_undecided", value_undecided(True))
    print("live_binding", live_binding())
    print("dead_local", dead_local())
    print("dead_global", dead_global())
    print("lambda_walrus_global", lambda_walrus_global())
    print("closure_local", closure_local())
    print("decided_return", decided_return(False))
    print("gen_dead_only", list(gen_dead_only()))
    print("coro_dead_only", asyncio.run(coro_dead_only()))
    print("walrus_scalar", walrus_scalar())
    print("match_none_or", match_none_or(None), match_none_guard(None),
          match_none_empty(None))
    print("own_read_after", own_read_after())
    calls.clear()
    print("non_member_call", non_member_call(), calls)
    calls.clear()
    print("elif_impure", elif_impure(False, Dog("e")), calls)
    print("false_head_chain", false_head_chain(2, Dog("f")),
          false_head_chain(1, Dog("f")), false_head_chain(0, Dog("f")))
    calls.clear()
    print("walrus_live", walrus_live(), calls)
    calls.clear()
    print("comp_impure", comp_impure(["c1", "c2"]), calls)
    print("while_impure", while_impure(1))
    calls.clear()
    print("while_true_return", while_true_return(Gate("wr", False)), calls)
    calls.clear()
    print("while_true_call", while_true_call(), calls)
    calls.clear()
    print("while_true_passes", while_true_passes(), calls)
    calls.clear()
    print("gen_impure", list(gen_impure(Dog("g"))), calls)
    calls.clear()
    print("coro_impure", asyncio.run(coro_impure(Dog("c"))), calls)
    print("while_dead_local", while_dead_local(Dog("w")))
    print("dead_capture_after", dead_capture_after(Dog("d")))
    print("guard_capture", guard_capture(5))
    print("guard_true", guard_true(6))
    print("arm_local", arm_local())
    print("ternary_arm_value", ternary_arm_value(True, ""),
          ternary_arm_value(False, ""))
    print("dead_global_decl", dead_global_decl(), shadowed)
    print("dead_nonlocal_decl", dead_nonlocal_decl())
    print("inner_scope_nonlocal", inner_scope_nonlocal())
    print("dead_match_global", dead_match_global(Dog("m")), shadowed)
    print("decl_ctor", DeclCtor(11).n, shadowed)
    calls.clear()
    print("match_no_arm", match_no_arm(Dog("n")), calls)
    try:
        r = no_arm_checked(None)
    except AttributeError:
        r = "raised"
    print("no_arm_checked", r, no_arm_checked(Tag(1, Bird(2))))
    try:
        r = fold_checked(None)
    except AttributeError:
        r = "raised"
    print("fold_checked", r, fold_checked(Tag(1, Bird(2))))
    print("del_after", del_after())
    print("del_rebind", del_rebind())
    print("del_cond_walrus", del_cond_walrus(False), del_cond_walrus(True))
    print("finally_arm", finally_arm())
    print("capture_rebind", capture_rebind(Animal("q"), "ab"))
    calls.clear()
    print("none_call_once", none_call_once(), calls)
    print("none_then_dead", none_then_dead(None))
    calls.clear()
    print("none_no_arm", none_no_arm(), calls)
    print("none_del", none_del(None))
    calls.clear()
    print("none_gen", list(none_gen()), calls)
    print("none_guard", none_guard(None, True), none_guard(None, False))
    calls.clear()
    print("none_guard_call", none_guard_call(True), none_guard_call(False),
          calls)
    calls.clear()
    printed = none_print()
    print("none_print", printed)
    printed = none_print_no_arm()
    print("none_print_no_arm", printed)
    calls.clear()
    # Bound before printing: a failing print argument would leave the
    # earlier ones on stdout (BUGS.md#print-arg-output-interleaves).
    try:
        er_ok = none_er("good")
        er_no_arm = none_er_no_arm("good")
        print("none_er", er_ok, er_no_arm, calls)
        er_bad = none_er("bad")
        print("none_er", er_bad)
    except Fault:
        print("none_er", "fault", calls)
    calls.clear()
    print("none_await", asyncio.run(none_await()),
          asyncio.run(none_await_no_arm()), calls)
    calls.clear()
    print("none_await_arm_awaits", asyncio.run(none_await_arm_awaits()), calls)
    calls.clear()
    print("none_await_arm_jumps", asyncio.run(none_await_arm_jumps()), calls)
    calls.clear()
    print("none_await_in_if", asyncio.run(none_await_in_if(True)),
          asyncio.run(none_await_in_if(False)), calls)
    print("walrus", walrus_subject(), walrus_stmt())
    print("none_tail", none_tail(None))
    print("none_field", none_field(NoneHolder(), True),
          none_field(NoneHolder(), False))
    calls.clear()
    print("guard_dead_chain", guard_dead_chain(1), guard_dead_chain(2), calls)
    calls.clear()
    print("guard_dead_union", guard_dead_union(3), guard_dead_union("s"),
          calls)
    calls.clear()
    print("guard_dead_switch", guard_dead_switch(1, True),
          guard_dead_switch(1, False), guard_dead_switch(2, False), calls)
    calls.clear()
    print("guard_dead_str", guard_dead_str("a"), guard_dead_str("eeeee"),
          guard_dead_str("z"), calls)
    # kin.py warns on its tests: a Ray would take the arms in CPython.
    print("mi", mi_isinstance(Fin()), mi_pattern(Fin()), mi_union(Fin()))


class Ray(Fin, Wing):
    def __init__(self) -> None:
        Fin.__init__(self)
        Wing.__init__(self)


main()

# module level: a decided `if` keeps its block, over its live arm, and its
# test runs; Griffin derives both classes.
if isinstance(Bat(), Bird):  # tpyc: warning(/'Griffin' derives both/)
    print("module", Bat().wings)
else:
    print("module", "bat")

module_x = 1
# module level: a decided `is None` test is its bool value.
if module_x is None:  # tpyc: ok
    print("module_none", module_x.lives)
else:
    print("module_none", "live")
assert module_x is not None, module_x.lives  # tpyc: ok
print("module_assert", "live")

# module level: a match with no arm left is what its subject runs.
calls.clear()
match mk("module_no_arm"):  # tpyc: warning(/non-exhaustive match on 'Dog'/)
    case Cat():
        print("module_no_arm", "cat")
print("module_no_arm", calls)

# module level: a match on a call returning None runs the call, then its
# arm.
calls.clear()
match none_effect():  # tpyc: ok
    case None:
        module_arm = "arm"
print("module_none_call", module_arm, calls)
