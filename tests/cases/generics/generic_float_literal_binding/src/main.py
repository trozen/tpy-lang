# A generic call whose T is learned from a float literal (bare, in a list or
# nested in a tuple) binds T to float, and its result is typed by that binding
# at every position.
import asyncio
from typing import Callable, Iterator
from tpy import float32, int32


def first_of[T](xs: list[T]) -> T:
    return xs[0]


def ident[T](x: T) -> T:
    return x


def pair_first[A, B](a: A, b: B) -> A:
    return a


def apply[T](x: T, f: Callable[[T], T]) -> T:
    return f(x)


def swap(t: tuple[int32, int32]) -> tuple[int32, int32]:
    return (t[1], t[0])


def triple(v: int32) -> int32:
    return v * 3


class C:
    base: float

    def __init__(self) -> None:
        self.base = 0.5

    def pick[T](self, xs: list[T]) -> T:
        return xs[0]

    def body(self) -> float:
        # method: a generic call inside a method body
        return first_of([1.5, 2.5]) + self.base  # tpyc: ok


class Bag[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def add(self, v: T) -> None:
        # right: at a reference-type T the list stores a copy where CPython
        # stores the caller's object
        self.items.append(v)  # tpyc: warning(/may copy T into owned storage/)


def gen() -> Iterator[float]:
    # generator: decl then yield
    v = first_of([1.5, 2.5])  # tpyc: ok
    yield v


async def co() -> float:
    # async: the call result returned from a coroutine (a list literal
    # argument here is BUGS.md#resumable-arg-temp-no-flush)
    return ident(1.5)  # tpyc: ok


def main():
    # free: print, decl, binop, expr-stmt
    print("free:", first_of([1.5, 2.5]))  # tpyc: ok
    v = first_of([1.5, 2.5])  # tpyc: ok type(float)
    print("free:", v)
    print("free:", first_of([1.5, 2.5]) + 1.0)  # tpyc: ok
    first_of([1.5, 2.5])  # tpyc: ok
    # free: scalar literal, inferred local, two params, kwarg
    print("free:", ident(1.5))  # tpyc: ok
    xs = [1.5, 2.5]
    print("free:", first_of(xs))  # tpyc: ok
    print("free:", pair_first(1.5, 2))  # tpyc: ok
    print("free:", ident(x=1.5))  # tpyc: ok
    # twin: int and str literals already bound a concrete T
    print("twin:", first_of([1, 2]), first_of(["a", "b"]))  # tpyc: ok

    # tuple_t: T bound to a tuple literal of float / int elements
    print("tuple_t:", ident((1.5, 2)))  # tpyc: ok
    print("tuple_t:", ident((1, 2)))  # tpyc: ok

    # method: generic method called with a float list literal
    c = C()
    print("method:", c.pick([1.5, 2.5]), c.body())  # tpyc: ok

    # closure: the call inside a nested function
    def inner() -> float:
        return first_of([1.5, 2.5]) * 2.0  # tpyc: ok
    print("closure:", inner())

    for g in gen():
        print("generator:", g)

    print("async:", asyncio.run(co()))

    # comprehension: the call as the element expression
    print("comprehension:", [first_of([1.5, 2.5]) for _ in range(2)])  # tpyc: ok

    try:
        # try_finally: the call inside a try body
        print("try_finally:", first_of([1.5, 2.5]))  # tpyc: ok
    finally:
        print("try_finally: done")

    n = 1
    match n:
        case 1:
            # match_arm: the call inside a match arm
            print("match_arm:", first_of([1.5, 2.5]))  # tpyc: ok
        case _:
            print("match_arm: other")

    # lambda_fn_slot: T from a float literal types the lambda parameter
    print("lambda_fn_slot:", apply(1.5, lambda v: v * 2.0))  # tpyc: ok
    print("lambda_fn_slot:", apply(1.5, f=lambda v: v * 2.0))  # tpyc: ok
    # lambda_fn_slot: a marker nested in a tuple binds T; the lambda's
    # parameter takes the finalized tuple type
    print("lambda_fn_slot:", apply((1, 2), lambda t: t))  # tpyc: ok
    print("lambda_fn_slot:", apply((1.5, 2), lambda t: t))  # tpyc: ok
    # lambda_fn_slot: the keyword spelling of the tuple-literal form
    print("lambda_fn_slot:", apply((1, 2), f=lambda t: t))  # tpyc: ok

    # named_fn_slot: a named function at the callable slot, T from a tuple
    # or scalar int literal (the `int`-annotated function is
    # BUGS.md#named-fn-at-generic-callable-slot-ill-formed)
    print("named_fn_slot:", apply((1, 2), swap))  # tpyc: ok
    print("named_fn_slot:", apply(1, triple))  # tpyc: ok

    # pending_instance: the first add() binds T of an unannotated instance
    b = Bag()
    b.add(1.5)  # tpyc: ok
    print("pending_instance:", b.items[0])

    # builtins: generic builtins over float literals
    print("builtins:", sum([1.5, 2.5]))  # tpyc: ok
    print("builtins:", sorted([2.5, 1.5]))  # tpyc: ok
    print("builtins:", set([1.5]))  # tpyc: ok
    print("builtins:", list([0.5]))  # tpyc: ok
    ws = [1.5, 2.5]
    print("builtins:", list(enumerate(ws)))  # tpyc: ok
    for row in [[0.5]]:  # tpyc: ok
        print("builtins:", row)
    print("builtins:", max(1.5, 2.5, key=lambda w: -w))  # tpyc: ok
    # builtins: list concatenation binds the operator's T from float literals
    print("builtins:", [1.5] + [2.5])  # tpyc: ok

    # float32_dest: a float32 destination narrows T / converts the result
    f: float32 = first_of([1.5, 2.5])  # tpyc: ok
    print("float32_dest:", f)
    h: float32 = ident(0.25)  # tpyc: ok
    print("float32_dest:", h)


main()

# global: an unannotated module-level float list, subscripted in a function
X = [0.15, 0.3]


def above(i: int) -> bool:
    return 0.2 < X[i]  # tpyc: ok


print("global:", above(0), above(1))
