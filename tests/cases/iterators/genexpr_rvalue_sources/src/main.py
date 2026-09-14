# A generator expression over an RVALUE source (dict view, combinator, generator
# call, Own[container] call, container-ctor call, literal) owns it: the source
# is built in place inside the closure's holder and the iterator seeds on the
# first pull -- every source a comprehension iterates, the genexpr iterates too,
# at every position, pulling a generator source lazily like CPython. A movable
# closure may be moved by an owning consumer (another lazy combinator) before
# its first pull; a non-movable one (an owning combinator over a generator call
# deletes its move ctor) is a located reject there
# (error_genexpr_nonmovable_into_owning).
# Excluded cells: a readonly receiver with a reference element
# (BUGS.md#genexpr-readonly-source-slot); a nested genexpr as the source
# (BUGS.md#genexpr-nested-source); a source call needing an arg temp
# (BUGS.md#genexpr-source-needs-arg-temp); the comprehension position, where
# a genexpr ELEMENT has no lowering at any source (BUGS.md#genexpr-comp-element).
from tpy import int32, readonly, Own, error_return, ReturnException
from typing import Iterator
import asyncio


class Node:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


class Err(Exception, ReturnException):
    pass


class Tally:
    d: dict[int32, int32]
    total: int32

    def __init__(self, d: Own[dict[int32, int32]]) -> None:
        self.d = d
        # ctor: a dict-view source off the `self.d` field receiver.
        self.total = sum(v for v in self.d.values())  # tpyc: ok

    def keys_sum(self) -> int32:
        # method: a dict-view source off the `self.d` field receiver.
        return sum(k for k in self.d.keys())  # tpyc: ok


class Guard:
    def __enter__(self) -> int32:
        return 1

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def gen() -> Iterator[int32]:
    yield 1
    yield 2
    yield 3


def noisy() -> Iterator[int32]:
    for i in range(3):
        print("noisy: pull", i)
        yield i


def ranged() -> Iterator[int32]:
    # a frame with frame_slot members (the range counters): non-copyable.
    for i in range(4):
        yield i


def four() -> Iterator[int32]:
    # the copyable twin: the owning zip copies its frame sources
    # (BUGS.md#owning-zip-copies-frame-source).
    yield 0
    yield 1
    yield 2
    yield 3


def make_list() -> Own[list[int32]]:
    return [4, 5, 6]


def borrow_list(xs: list[int32]) -> list[int32]:
    return xs


def ro_values(d: readonly[dict[int32, int32]]) -> int32:
    # readonly receiver, scalar element: the `auto_readonly` value binds a copy.
    return sum(v for v in d.values())  # tpyc: ok


def ro_items(d: readonly[dict[int32, int32]]) -> int32:
    return sum(k + v for k, v in d.items())  # tpyc: ok


def framegen(xs: list[int32]) -> Iterator[int32]:
    # generator body: the source and the captured `xs` are frame members.
    yield sum(x for x in xs)  # tpyc: ok
    yield sum(a + b for a, b in zip(xs, xs))  # tpyc: ok
    yield sum(x for x in gen())  # tpyc: ok


async def aio(xs: list[int32]) -> int32:
    await asyncio.sleep(0)
    # async body: an enumerate source with an unpack head.
    return sum(i * x for i, x in enumerate(xs))  # tpyc: ok


@error_return(Err)
def fallible(d: dict[int32, int32]) -> int32:
    # @error_return body.
    t = sum(v for v in d.values())  # tpyc: ok
    if t < 0:
        raise Err
    return t


def by_match(n: int32, xs: list[int32]) -> int32:
    match n:
        case 1:
            # match arm: a reversed source.
            return sum(x for x in reversed(xs))  # tpyc: ok
        case _:
            return 0


def big(v: int32) -> bool:
    return v > 1


def triple(v: int32) -> int32:
    return v * 3


def drain(it: Iterator[int32]) -> int32:
    # structural Iterator[T] protocol param, fed an rvalue-source genexpr.
    t = 0
    for v in it:
        t += v
    return t


def byte_sum(data: bytes) -> int32:
    # bytes PARAM source (a borrowed view).
    return sum(b - 96 for b in data)  # tpyc: ok


def closure_pos(d: dict[int32, int32], k: int32) -> int32:
    def inner() -> int32:
        # closure position: the nested def reads the outer local `k`.
        return sum(v + k for v in d.values())  # tpyc: ok

    return inner()


XS: list[int32] = [1, 2, 3]
# module level: a combinator source over a global.
print("module", sum(x for x in reversed(XS)))  # tpyc: ok
print("module_lit", sum(x for x in [7, 8]))  # tpyc: ok


def main() -> None:
    d = {1: 2, 3: 4}
    xs: list[int32] = [1, 2, 3]
    ys: list[int32] = [10, 20, 30]
    # free function: every rvalue source shape, scalar elements.
    print("values", sum(v for v in d.values()))  # tpyc: ok
    print("keys", sum(k for k in d.keys()))  # tpyc: ok
    print("items", sum(k * v for k, v in d.items()))  # tpyc: ok
    print("zip", sum(a + b for a, b in zip(xs, ys)))  # tpyc: ok
    print("enumerate", sum(i * x for i, x in enumerate(xs)))  # tpyc: ok
    print("reversed", list(x for x in reversed(xs)))  # tpyc: ok
    print("filter", list(x for x in filter(big, xs)))  # tpyc: ok
    print("map", list(x for x in map(triple, xs)))  # tpyc: ok
    print("gen", sum(x for x in gen()))  # tpyc: ok
    print("ranged", sum(x for x in ranged()))  # tpyc: ok
    # non-movable owning combinators over generator calls: zip pulls the
    # longer side once past the shorter one's end, like CPython.
    print("zip_gens", sum(a + b for a, b in zip(gen(), four())))  # tpyc: ok
    print("zip_gens_long", sum(a + b for a, b in zip(four(), gen())))  # tpyc: ok
    print("enumerate_gen", sum(i * x for i, x in enumerate(gen())))  # tpyc: ok
    # a MOVABLE genexpr moved into a combinator's owning flavor before its first
    # pull: over a list name, and over a generator call (the frame is unstarted).
    print("enumerate_genexpr", sum(i * v for i, v in enumerate(x * 2 for x in xs)))  # tpyc: ok
    print("zip_genexpr_gen", sum(a * b for a, b in zip((x for x in gen()), xs)))  # tpyc: ok
    print("filter_gen", list(x for x in filter(big, gen())))  # tpyc: ok
    print("own_call", sum(x for x in make_list()))  # tpyc: ok
    print("borrow_call", sum(x for x in borrow_list(xs)))  # tpyc: ok
    # container-ctor call source (`list(xs)` / `sorted(xs)`): the built container
    # is an rvalue held in the closure state like the Own[list] call above.
    print("ctor_call_list", sum(x for x in list(xs)))  # tpyc: ok
    print("ctor_call_sorted", list(x for x in sorted(ys) if x > 10))  # tpyc: ok
    print("literal", sum(x * x for x in [1, 2, 3, 4]))  # tpyc: ok
    print("literal_unpack", sum(a for a, b in [(1, 2), (3, 4)]))  # tpyc: ok
    # filters on an rvalue source: a filtered-out element advances in place.
    print("zip_filter", sum(a for a, b in zip(xs, ys) if b > 10))  # tpyc: ok
    print("gen_filter", list(x for x in gen() if x != 2))  # tpyc: ok
    # deferred advance: two consecutive filtered-out elements, and the last
    # element filtered out, over one-pass and container rvalue sources.
    print("filter_tail_gen", list(x for x in ranged() if x < 2))  # tpyc: ok
    print("filter_tail_map", list(x for x in map(triple, xs) if x < 6))  # tpyc: ok
    print("filter_mid_view", list(v for v in d.values() if v != 4))  # tpyc: ok
    print("drain", drain(x * 2 for x in gen()))  # tpyc: ok
    print("byte_sum", byte_sum(b"abc"))
    print("closure", closure_pos(d, 10))
    print("any", any(v > 3 for v in d.values()))  # tpyc: ok
    print("all", all(v > 3 for v in d.values()))  # tpyc: ok
    # bytes NAME source (borrowed): its uint8 element binds like any scalar,
    # so the str source's sibling no longer rejects (the yield stays uint8,
    # so the summed offsets keep the total in range).
    data = b"abc"
    print("bytes", sum(b - 96 for b in data))  # tpyc: ok
    print("bytes_dict", len(dict((b, 0.0) for b in data)))  # tpyc: ok
    # str element: the view aliases the source element through the yield.
    names = {1: "ab", 2: "cd"}
    print("join", ", ".join(s.upper() for s in names.values()))  # tpyc: ok
    # reference element off a dict view: the yield borrows, so a mutation
    # through the loop var reaches the dict.
    nodes = {1: Node(1), 2: Node(2)}
    for n in (n for n in nodes.values()):  # tpyc: ok
        n.val += 100
    print("ref_mutate", [n.val for n in nodes.values()])
    # lazy pull: the source generator runs one element per consumer step.
    for v in (x * 10 for x in noisy()):  # tpyc: ok
        print("noisy: got", v)
    print("ro_values", ro_values(d))
    print("ro_items", ro_items(d))
    t = Tally({1: 2, 3: 4})
    print("ctor", t.total)
    print("method", t.keys_sum())
    print("frame", list(framegen(xs)))
    print("async", asyncio.run(aio(xs)))
    with Guard() as g:
        # with body.
        print("with", sum(v + g for v in d.values()))  # tpyc: ok
    try:
        # try/finally body.
        print("try", sum(x for x in map(triple, xs)))  # tpyc: ok
    finally:
        print("finally", sum(k for k in d.keys()))  # tpyc: ok
    try:
        print("error_return", fallible(d))
    except Err:
        print("error_return: raised")
    print("match", by_match(1, xs))


main()
