# `except StopIteration` over an @error_return call, at the positions that
# render through the goto dispatch: the shapes a generator or `async def`
# frame still supports (a try holding no suspension and no transfer leaving
# it), a decomposed try whose ReturnException handler no call can enter (a
# dead catch), and the two nested. The generators yield list elements that are
# mutated after the yield and read back through the list, so an aliasing yield
# is told apart from a copy.
import asyncio
from typing import Iterator
from tpy import int32, error_return, Own


class Node:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Indices:
    n: int32
    limit: int32

    def __init__(self, limit: int32) -> None:
        self.n = 0
        self.limit = limit

    def __iter__(self) -> "Indices":
        return self

    @error_return(StopIteration)
    def __next__(self) -> int32:
        if self.n >= self.limit:
            raise StopIteration
        cur = self.n
        self.n += 1
        return cur


def fresh() -> Own[list[Node]]:
    return [Node(1), Node(2)]


# free function: the try is the whole loop body, nothing leaves it
def total(it: Iterator[int32]) -> int32:
    acc = 0
    while True:
        try:
            i = next(it)  # tpyc: ok
        except StopIteration:
            break
        acc += i
    return acc


# @error_return body: a handled call beside a propagating one
@error_return(StopIteration)
def first_two(it: Iterator[int32]) -> int32:
    a = next(it)
    try:
        b = next(it)  # tpyc: ok
    except StopIteration:
        return a
    return a + b


# the remedy the frame reject names: the try lives in a plain helper, whose
# `return` is the handled call itself
def pull(it: Iterator[int32]) -> int32 | None:
    try:
        return next(it)  # tpyc: ok
    except StopIteration:
        return None


# generator: the helper form, driving a loop the handler used to break out of
def walk(items: list[Node]) -> Iterator[Node]:
    src = Indices(len(items))
    while True:
        i = pull(src)
        if i is None:
            break
        node = items[i]
        yield node


# generator: leaf try -- no suspension inside it, and the handler's `return`
# ends the frame rather than leaving the try
def head(items: list[Node], it: Iterator[int32]) -> Iterator[Node]:
    try:
        i = next(it)  # tpyc: ok
    except StopIteration:
        return
    node = items[i]
    yield node


# generator: a decomposed try (the `yield` is inside it) whose handler no call
# can enter -- nothing in the body returns a StopIteration, so the handler is
# dead and the frame keeps compiling it as an unreachable catch
def dead_pass(items: list[Node]) -> Iterator[Node]:
    for node in items:
        try:  # tpyc: ok
            yield node
        except StopIteration:
            pass


# same, handler `break`s out of the enclosing loop
def dead_break(items: list[Node]) -> Iterator[Node]:
    for node in items:
        try:  # tpyc: ok
            yield node
        except StopIteration:
            break


# same, handler ends the frame
def dead_return(items: list[Node]) -> Iterator[Node]:
    for node in items:
        try:  # tpyc: ok
            yield node
        except StopIteration:
            return


# generator: the nested face -- a LIVE handled try (leaf: no suspension in it,
# handler falls through) inside a decomposed dead-handler outer try. Only the
# inner one holds a call its handler can enter, so it renders through the goto
# dispatch while the outer stays an unreachable catch around the `yield`.
def nested(items: list[Node], it: Iterator[int32]) -> Iterator[Node]:
    for node in items:
        try:  # tpyc: ok
            yield node
            try:
                i = next(it)  # tpyc: ok
            except StopIteration:
                i = -1
            print("nested_inner", i)
        except StopIteration:
            break


# async def: leaf try -- no suspension inside it and the handler falls through,
# so the handled call renders through the goto dispatch inside the frame
async def a_first(it: Iterator[int32]) -> int32:
    try:
        v = next(it)  # tpyc: ok
    except StopIteration:
        v = -1
    await asyncio.sleep(0.0)
    return v


# async def: the dead-handler try, decomposed by the `await` in its body
async def a_dead(n: int32) -> int32:
    acc = 0
    for i in range(n):
        try:  # tpyc: ok
            await asyncio.sleep(0.0)
            acc += i
        except StopIteration:
            break
    return acc


async def a_main() -> None:
    print("async_leaf", await a_first(Indices(1)), await a_first(Indices(0)))
    print("async_dead", await a_dead(3))


class Counter:
    limit: int32

    def __init__(self, limit: int32) -> None:
        self.limit = limit

    # method: the discarded-call form of the same try
    def count(self) -> int32:
        src = Indices(self.limit)
        seen = 0
        while True:
            try:
                next(src)  # tpyc: ok
            except StopIteration:
                break
            seen += 1
        return seen


# module-level statement: the same try outside any function
m_nodes = fresh()
m_src = Indices(2)
m_seen = 0
while True:
    try:
        m_i = next(m_src)  # tpyc: ok
    except StopIteration:
        break
    m_nodes[m_i].v += 100
    m_seen += 1


def main() -> None:
    print("module", m_seen, m_nodes[0].v, m_nodes[1].v)

    src = Indices(4)
    print("free", total(src))

    src2 = Indices(2)
    try:
        print("error_return", first_two(src2))  # tpyc: ok
    except StopIteration:
        print("error_return", "stopped")

    nodes = fresh()
    src3 = Indices(2)
    for n in head(nodes, src3):
        # Mutating the yielded node must reach the list element it came from.
        n.v = 90
    print("gen_leaf", nodes[0].v, nodes[1].v)

    # Drive the leaf try to exhaustion so its handler runs: Indices(0) makes
    # the first next() fail, the handler returns, the generator yields nothing.
    nodes_empty = fresh()
    empty_seen = 0
    for n in head(nodes_empty, Indices(0)):
        empty_seen += 1
    print("gen_leaf_empty", empty_seen, nodes_empty[0].v, nodes_empty[1].v)

    nodes2 = fresh()
    for n in walk(nodes2):
        n.v += 10
    print("gen_helper", nodes2[0].v, nodes2[1].v)

    print("method", Counter(3).count())

    dead_nodes = fresh()
    for n in dead_pass(dead_nodes):
        n.v += 90
    print("dead_pass", dead_nodes[0].v, dead_nodes[1].v)

    # Indices(1) drives the inner handler both ways: the first resume unwraps a
    # value, the second takes the except leg.
    nested_nodes = fresh()
    for n in nested(nested_nodes, Indices(1)):
        n.v += 5
    print("nested", nested_nodes[0].v, nested_nodes[1].v)

    print("dead_break", [n.v for n in dead_break(fresh())])
    print("dead_return", [n.v for n in dead_return(fresh())])

    asyncio.run(a_main())


main()
