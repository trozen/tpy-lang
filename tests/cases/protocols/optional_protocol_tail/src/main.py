# The statements after a statement that narrows a protocol template
# parameter (`P | None`, a protocol union) to some of its alternatives (an
# early-exit guard at any nesting, an `assert isinstance`) are instantiated
# only for those alternatives -- never at the None default when the
# narrowing excludes None, and still there when it does not.
from typing import Callable, Iterable, Iterator, Sized, overload
from tpy import ReturnException, error_return, int32


# early return, no else
def early_return(x: Sized | None = None) -> int32:
    if not isinstance(x, Sized):  # tpyc: ok
        return -1
    return len(x)


# early raise, no else
def early_raise(x: Sized | None = None) -> int32:
    if not isinstance(x, Sized):  # tpyc: ok
        raise ValueError("none")
    return len(x)


# explicit else that falls through: the rest follows the else's statements
def explicit_else(x: Sized | None = None) -> int32:
    if not isinstance(x, Sized):  # tpyc: ok
        return -1
    else:
        print("explicit_else: present")
    return len(x)


# the then arm survives, the else returns
def then_survives(x: Sized | None = None) -> int32:
    if isinstance(x, Sized):  # tpyc: ok
        print("then_survives: some")
    else:
        return -2
    return len(x)


# `continue` in a loop: the rest of the loop body
def loop_continue(items: Sized | None = None) -> int32:
    total = 0
    for k in range(3):
        if not isinstance(items, Sized):  # tpyc: ok
            continue
        total += len(items) + k
    return total


# typing.overload body, one per-stub fold in the rest
@overload
def ovl(items: Sized | None, x: int32) -> int32: ...
@overload
def ovl(items: Sized | None, x: str) -> int32: ...
def ovl(items: Sized | None, x: int32 | str) -> int32:
    if not isinstance(items, Sized):  # tpyc: ok
        return -1
    if isinstance(x, str):
        return len(x) + 100
    return x + len(items)


# `is not None` guard
def not_none(x: Sized | None = None) -> int32:
    if x is not None:  # tpyc: ok
        print("not_none: some", len(x))
    else:
        return -2
    return len(x)


# assert isinstance: the failing arm raises, the passing arm holds the rest
def asserted(x: Sized | None = None) -> int32:
    assert isinstance(x, Sized)  # tpyc: ok
    return len(x)


def size(s: Sized) -> int32:
    return len(s)


# the rest prints the parameter and passes it on
def print_and_pass(items: Sized | None = None) -> int32:
    if not isinstance(items, Sized):  # tpyc: ok
        return -1
    print("print_and_pass:", items)
    return size(items)


# the guard is the else arm's nested if (an elif); the rest follows the outer if
def elif_guard(x: Sized | None = None, k: int32 = 0) -> int32:
    if k < 0:
        return 0
    elif not isinstance(x, Sized):  # tpyc: ok
        return -1
    return len(x) + k


# the guard sits in a runtime if's arm; the rest follows the outer if
def nested_then(x: Sized | None = None, f: bool = True) -> int32:
    if f:
        if not isinstance(x, Sized):  # tpyc: ok
            return -1
    else:
        return 0
    return len(x)


# the guard sits in a try body; the rest follows the try
def in_try(x: Sized | None = None) -> int32:
    try:
        if not isinstance(x, Sized):  # tpyc: ok
            return -1
    finally:
        print("in_try: finally")
    return len(x)


class Ctx:
    def __enter__(self) -> "Ctx":
        print("in_with: enter")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print("in_with: exit")


# the guard sits in a with body; the rest follows the with
def in_with(x: Sized | None = None) -> int32:
    with Ctx():
        if not isinstance(x, Sized):  # tpyc: ok
            return -1
    return len(x)


class Counter:
    base: int32

    def __init__(self) -> None:
        self.base = 10

    # a method with an Optional[Protocol] parameter and an early return
    def count(self, x: Sized | None = None) -> int32:
        if not isinstance(x, Sized):  # tpyc: ok
            return -1
        return self.base + len(x)


# `break` in a loop: the rest of the loop body
def loop_break(items: Sized | None = None) -> int32:
    total = 0
    for k in range(3):
        if not isinstance(items, Sized):  # tpyc: ok
            break
        total += len(items) + k
    return total


# a local declared in the code after the guard
def local_after(x: Sized | None = None) -> int32:
    if not isinstance(x, Sized):  # tpyc: ok
        return -1
    n = len(x)  # tpyc: ok
    return n


# two Optional[Protocol] parameters guarded one after the other
def two_params(a: Sized | None = None, b: Sized | None = None) -> int32:
    if not isinstance(a, Sized):  # tpyc: ok
        return -1
    if not isinstance(b, Sized):  # tpyc: ok
        return -2
    return len(a) * 10 + len(b)


def values() -> Iterator[int32]:
    yield 1


# a union keeps None after ruling out Sized: the rest runs at the None default
def union_rest(x: Sized | Iterable[int32] | None = None) -> int32:
    if isinstance(x, Sized):  # tpyc: ok
        return 1
    print("union_rest: rest")
    return 2


# the same through a negated assert
def union_assert(x: Sized | Iterable[int32] | None = None) -> None:
    assert not isinstance(x, Sized)  # tpyc: ok
    print("union_assert: rest")


# a required protocol union narrowed to one protocol
def required_union(x: Sized | Iterable[int32]) -> int32:
    if not isinstance(x, Sized):  # tpyc: ok
        return -1
    return len(x)


def required_assert(x: Sized | Iterable[int32]) -> int32:
    assert isinstance(x, Sized)  # tpyc: ok
    return len(x)


# neighbour: the inner guard's enclosing else falls through, so nothing is
# narrowed after the outer if and the rest runs at the None default
def inner_else(x: Sized | None = None, f: bool = True) -> int32:
    if f:
        if not isinstance(x, Sized):  # tpyc: ok
            return -1
    else:
        print("inner_else: other")
    return 5


# neighbour: a guard in a for body narrows nothing after the loop
def for_after(x: Sized | None = None) -> int32:
    for k in range(2):
        if not isinstance(x, Sized):  # tpyc: ok
            continue
        print("for_after:", len(x) + k)
    return 9


# neighbour: the except swallows the guard's raise, so the rest runs at None
def caught(x: Sized | None = None) -> int32:
    try:
        if not isinstance(x, Sized):  # tpyc: ok
            raise ValueError("none")
    except ValueError:
        print("caught: caught")
    return 4


class Holder:
    n: int32

    # a constructor with an early return
    def __init__(self, x: Sized | None = None) -> None:
        self.n = -1
        if not isinstance(x, Sized):  # tpyc: ok
            return
        self.n = len(x)


class Missing(Exception, ReturnException):
    pass


# an @error_return body
@error_return(Missing)
def er_body(x: Sized | None = None) -> int32:
    if not isinstance(x, Sized):  # tpyc: ok
        raise Missing()
    return len(x)


# the guard in a match arm, the rest after the match
def match_arm(x: Sized | None = None, k: int32 = 0) -> int32:
    match k:
        case 0:
            if not isinstance(x, Sized):  # tpyc: ok
                return -1
        case _:
            return -2
    return len(x)


# a closure over the narrowed parameter in the rest
def closure(x: Sized | None = None) -> int32:
    if not isinstance(x, Sized):  # tpyc: ok
        return -1
    g: Callable[[], int32] = lambda: len(x)  # tpyc: warning(/may keep its own copy of 'x'/)
    return g()


# a nested def guarding the captured parameter
def nested_def(x: Sized | None = None) -> int32:
    def inner(k: int32) -> int32:
        if not isinstance(x, Sized):  # tpyc: ok
            return -1
        return len(x) + k
    return inner(1)


# the same parameter guarded twice: the second guard adds no wrap
def twice(x: Sized | None = None) -> int32:
    if not isinstance(x, Sized):  # tpyc: ok
        return -1
    if not isinstance(x, Sized):  # tpyc: ok
        return -2
    return len(x)


# a non-protocol Optional guard beside a protocol one: only x is wrapped
def with_k(x: Sized | None = None, k: int32 | None = None) -> int32:
    if k is None:  # tpyc: ok
        return -3
    if not isinstance(x, Sized):  # tpyc: ok
        return -1
    return len(x) + k


# guard and wrap render one test: on a nullable param a concept term is
# kept off `std::nullptr_t` (Iterable accepts it), so the None default takes
# the guard and the rest is never reached there
def guard_iter(x: Sized | Iterable[int32] | None = None) -> int32:
    if not isinstance(x, Iterable):  # tpyc: ok
        return -1
    return 5


# the same through an assert: the None default fails it
def assert_iter(x: Sized | Iterable[int32] | None = None) -> int32:
    assert isinstance(x, Iterable)  # tpyc: ok
    return 3


# `isinstance(None, Iterable)` is False, so the None default runs the rest
def iter_rest(x: Sized | Iterable[int32] | None = None) -> None:
    if isinstance(x, Iterable):  # tpyc: ok
        print("iter_rest: iterable")
        return
    print("iter_rest: rest")


# two facts from one if/elif, each ruling Sized out of one nullable param;
# the first is then iterated
def two_facts(a: Sized | Iterable[int32] | None = None,
              b: Sized | Iterable[int32] | None = None) -> int32:
    if isinstance(a, Sized):  # tpyc: ok
        return 10
    elif isinstance(b, Sized):  # tpyc: ok
        return 20
    if a is not None:  # tpyc: ok
        for n in a:
            return n
    return 30


class Len1:
    def __len__(self) -> int32:
        return 1


# neighbour: a list is both Sized and Iterable -- the first guard takes it
def both(x: Sized | Iterable[int32] | None = None) -> int32:
    if isinstance(x, Sized):  # tpyc: ok
        return len(x)
    if isinstance(x, Iterable):  # tpyc: ok
        return -2
    return -3


# neighbour: the not-None guard on a protocol union, rest after it
def not_none_union(x: Sized | Iterable[int32] | None = None) -> int32:
    if x is not None:  # tpyc: ok
        print("not_none_union: present")
    else:
        return -1
    return 2


def main() -> None:
    xs = [1, 2, 3]
    print("early_return", early_return(), early_return(xs))
    try:
        early_raise()
    except ValueError:
        print("early_raise: raised")
    print("early_raise", early_raise(xs))
    r = explicit_else()
    print("explicit_else", r)
    r = explicit_else(xs)
    print("explicit_else", r)
    r = then_survives(xs)
    print("then_survives", r, then_survives())
    print("loop_continue", loop_continue(xs), loop_continue())
    print("ovl", ovl(xs, 4), ovl(xs, "ab"), ovl(None, 4), ovl(None, "ab"))
    r = not_none(xs)
    print("not_none", r, not_none())
    print("asserted", asserted(xs))
    try:
        asserted()
    except AssertionError:
        print("asserted: raised")
    r = print_and_pass(xs)
    print("print_and_pass", r, print_and_pass())
    print("elif_guard", elif_guard(), elif_guard(xs), elif_guard(xs, -1))
    print("nested_then", nested_then(), nested_then(xs), nested_then(xs, False))
    r = in_try()
    print("in_try", r)
    r = in_try(xs)
    print("in_try", r)
    r = in_with()
    print("in_with", r)
    r = in_with(xs)
    print("in_with", r)
    c = Counter()
    print("method", c.count(), c.count(xs))
    print("loop_break", loop_break(xs), loop_break())
    print("local_after", local_after(), local_after(xs))
    ys = [7]
    print("two_params", two_params(), two_params(xs), two_params(xs, ys))
    g1 = values()
    r = union_rest()
    print("union_rest", r, union_rest(xs))
    r = union_rest(g1)
    print("union_rest", r)
    union_assert()
    g2 = values()
    union_assert(g2)
    try:
        union_assert(xs)
    except AssertionError:
        print("union_assert: raised")
    g3 = values()
    print("required_union", required_union(g3), required_union(xs))
    g4 = values()
    try:
        required_assert(g4)
    except AssertionError:
        print("required_assert: raised")
    print("required_assert", required_assert(xs))
    r = inner_else(f=False)
    print("inner_else", r, inner_else(), inner_else(xs))
    r = for_after()
    print("for_after", r)
    r = for_after(xs)
    print("for_after", r)
    r = caught()
    print("caught", r)
    r = caught(xs)
    print("caught", r)
    print("ctor", Holder().n, Holder(xs).n)
    try:
        er_body()
    except Missing:
        print("er_body: missing")
    try:
        print("er_body", er_body(xs))
    except Missing:
        print("er_body: missing")
    print("match_arm", match_arm(), match_arm(xs), match_arm(xs, 1))
    print("closure", closure(), closure(xs))
    print("nested_def", nested_def(), nested_def(xs))
    print("twice", twice(), twice(xs))
    print("with_k", with_k(xs), with_k(k=2), with_k(xs, 2))
    r = guard_iter()
    print("guard_iter", guard_iter(xs), r)
    print("assert_iter", assert_iter(xs))
    try:
        r = assert_iter()
        print("assert_iter", r)
    except AssertionError:
        print("assert_iter: raised")
    iter_rest()
    iter_rest(xs)
    one = Len1()
    print("two_facts", two_facts(one), two_facts(), two_facts(b=one))
    g5 = values()
    print("both", both(xs), both(), both(g5))
    r = not_none_union(xs)
    print("not_none_union", r, not_none_union())


main()
