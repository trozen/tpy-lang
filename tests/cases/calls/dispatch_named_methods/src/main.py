# Every written call to a method or operator @dispatch set names the variant
# sema picked (`b.put__Tag(t)`, `(b).__mul____int(...)`); one section per
# position, each mutating a reference argument the caller then observes.
import asyncio
from typing import Callable, Iterator
from tpy import basic_slice, dispatch, error_return, int32, readonly, ReturnException, Span


class Tag:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0


class Bag:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    @dispatch
    def put(self, t: Tag) -> str:
        t.hits += 1
        return "tag"

    @dispatch
    def put(self, x: int32) -> str:
        self.n += x
        return "int32"

    @dispatch
    def __mul__(self, k: int) -> int32:
        return 1

    @dispatch
    def __mul__(self, k: float) -> int32:
        return 2

    @dispatch
    def __rmul__(self, k: int) -> int32:
        return 10

    @dispatch
    def __rmul__(self, k: float) -> int32:
        return 20

    @dispatch
    def __lt__(self, k: int) -> bool:
        return True

    @dispatch
    def __lt__(self, k: float) -> bool:
        return False

    @dispatch
    def __gt__(self, k: int) -> bool:
        return True

    @dispatch
    def __gt__(self, k: float) -> bool:
        return False

    @dispatch
    def __contains__(self, x: int32) -> bool:
        return x == self.n

    @dispatch
    def __contains__(self, x: str) -> bool:
        return x == "bag"

    @dispatch
    def __getitem__(self, i: int32) -> int32:
        return i + self.n

    @dispatch
    def __getitem__(self, k: str) -> int32:
        return len(k)


class Row:
    data: list[int32]

    def __init__(self) -> None:
        self.data = [1, 2, 3, 4]

    @dispatch
    def __getitem__(self, i: int32) -> int32:
        return self.data[i]

    @dispatch
    def __getitem__(self, s: basic_slice) -> Span[readonly[int32]]:
        return self.data[s]


class Bad(Exception, ReturnException):
    pass


def tick(calls: list[str], label: str) -> int:
    calls.append(label)
    return 2


def free_function() -> None:
    b = Bag()
    t = Tag()
    # The method set: each call names its variant.
    print("free_function:", b.put(t), b.put(3), t.hits, b.n)  # tpyc: ok
    # Operator, reflected operator, `in` and a subscript set.
    print("free_function:", b * 3, b * 0.5, 3 * b, 0.5 * b)  # tpyc: ok
    print("free_function:", 3 in b, "bag" in b, b[1], b["ab"])  # tpyc: ok
    r = Row()
    # A subscript set over an index and a slice.
    part = r[1:3]  # tpyc: ok
    print("free_function:", r[1], len(part), part[0])  # tpyc: ok


class Holder:
    b: Bag

    def __init__(self) -> None:
        self.b = Bag()

    def run(self, t: Tag) -> str:
        # Method: a field receiver.
        return f"{self.b.put(t)} {self.b * 2}"  # tpyc: ok


def method() -> None:
    t = Tag()
    print("method:", Holder().run(t), t.hits)


def gen(b: Bag, t: Tag) -> Iterator[str]:
    # Generator: the pick across a suspension.
    yield b.put(t)  # tpyc: ok
    yield f"{2.5 * b}"  # tpyc: ok


def generator() -> None:
    b = Bag()
    t = Tag()
    for r in gen(b, t):
        print("generator:", r)
    print("generator:", t.hits)


async def coro(b: Bag, t: Tag) -> str:
    await asyncio.sleep(0)
    # Async: after an await.
    return f"{b.put(t)} {b * 1}"  # tpyc: ok


def async_def() -> None:
    b = Bag()
    t = Tag()
    print("async:", asyncio.run(coro(b, t)), t.hits)


def comprehension() -> None:
    b = Bag()
    t = Tag()
    # Comprehension: the element expression.
    print("comprehension:", [b.put(t) for _ in range(2)], [i * b for i in range(2)], t.hits)  # tpyc: ok


def closure() -> None:
    b = Bag()
    t = Tag()

    def inner() -> str:
        # Closure: captured receiver and argument.
        return f"{b.put(t)} {b * 4}"  # tpyc: ok

    print("closure:", inner(), t.hits)


class Ctx:
    def __enter__(self) -> "Ctx":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def with_body() -> None:
    b = Bag()
    t = Tag()
    with Ctx():
        # With body.
        print("with:", b.put(t), 7 in b, t.hits)  # tpyc: ok


def try_finally() -> None:
    b = Bag()
    t = Tag()
    try:
        # try / finally.
        print("try:", b.put(t), b[2])  # tpyc: ok
    finally:
        print("try:", t.hits, b.put(5), b.n)  # tpyc: ok


@error_return(Bad)
def checked(b: Bag, t: Tag) -> str:
    if b.n < 0:
        raise Bad()
    # @error_return body.
    return f"{b.put(t)} {3.5 * b}"  # tpyc: ok


def error_return_body() -> None:
    b = Bag()
    t = Tag()
    try:
        print("error_return:", checked(b, t), t.hits)
    except Bad:
        print("error_return: raised")


def match_arm(k: int32) -> None:
    b = Bag()
    t = Tag()
    match k:
        case 1:
            # match arm.
            print("match:", b.put(t), b * 1, "x" in b, t.hits)  # tpyc: ok
        case _:
            print("match: other")


class Built:
    out: str

    def __init__(self, b: Bag, t: Tag) -> None:
        # Constructor body.
        self.out = f"{b.put(t)} {b * 2}"  # tpyc: ok


def constructor_body() -> None:
    b = Bag()
    t = Tag()
    print("constructor:", Built(b, t).out, t.hits)


class SubBag(Bag):
    def __init__(self) -> None:
        super().__init__()

    def run(self, t: Tag) -> str:
        # The base's set through super() and through the class.
        return f"{super().put(1)} {Bag.put(self, t)} {self.n}"  # tpyc: ok


def base_set() -> None:
    t = Tag()
    print("base:", SubBag().run(t), t.hits)


def lazy() -> None:
    b = Bag()
    calls: list[str] = []
    flag = len(calls) > 0
    # A reflected set operator with a call operand: its member call runs the
    # right operand first (BUGS.md#subexpression-right-to-left-eval), so the
    # output reads only what runs, never the order.
    r = tick(calls, "left") * b  # tpyc: ok
    # Never run on a branch not taken (a logical right operand, an arm).
    r_and = flag and tick(calls, "and") * b == 10  # tpyc: ok
    r_arm = tick(calls, "arm") * b if flag else 0  # tpyc: ok
    print("lazy:", r, r_and, r_arm, calls)
    # A chained comparison over set operators.
    r_chain = tick(calls, "chain") < b < 5  # tpyc: ok
    print("lazy:", r_chain, calls)


class Animal:
    def __init__(self) -> None:
        pass


class Dog(Animal):
    def __init__(self) -> None:
        super().__init__()


class Vet:
    seen: int32

    def __init__(self) -> None:
        self.seen = 0

    @dispatch
    def g(self, a: Animal) -> str:
        self.seen += 1
        return "animal"

    @dispatch
    @readonly
    def g(self, d: Dog) -> str:
        return "dog"


def receiver_kind() -> None:
    v = Vet()
    d = Dog()
    # A mutating variant on a mutable receiver ranks over the readonly one:
    # g(Animal) runs (declared first, so CPython's stub, which runs the first
    # declared variant that applies, runs it too:
    # BUGS.md#stub-first-applicable-cannot-split-calls).
    print("receiver_kind:", v.g(d), v.seen)  # tpyc: ok


def main() -> None:
    free_function()
    method()
    generator()
    async_def()
    comprehension()
    closure()
    with_body()
    try_finally()
    error_return_body()
    match_arm(1)
    lazy()
    receiver_kind()
    constructor_body()
    base_set()


main()
mod_b = Bag()
mod_t = Tag()
# Module-level statement.
print("module:", mod_b.put(mod_t), mod_b * 3, 4 in mod_b, mod_t.hits)  # tpyc: ok
