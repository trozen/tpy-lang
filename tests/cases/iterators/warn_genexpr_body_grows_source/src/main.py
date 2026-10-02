# A generator expression's body that may grow or rewrite a capture (or its own
# source) is checked like the `for` or call it stands for. Warned code never runs.
from tpy import int32
from typing import Iterator
import asyncio


gsrc = [1, 2, 3]
gother = [0]


def grow(ys: list[int32]) -> bool:
    ys.append(7)
    return True


def grow_pairs(qs: list[tuple[int32, int32]]) -> bool:
    qs.append((0, 0))
    return True


def add_to(s: set[int32], x: int32) -> bool:
    s.add(x)
    return True


class Rec:
    name: str
    k: int32

    def __init__(self, name: str, k: int32) -> None:
        self.name = name
        self.k = k


    def set_name(self) -> bool:
        self.name = "a replacement long enough to leave the small-string buffer"
        return True


def rename(r: Rec) -> bool:
    r.name = "a replacement long enough to leave the small-string buffer"
    return True


def in_loop_callee(xs: list[int32], big: bool) -> int32:
    t = 0
    # free function: the body grows the iterated capture through a callee
    for v in xs:
        t += v + sum(1 for _ in range(1) if big and grow(xs))  # tpyc: warning(/Borrowed container 'xs' is mutated by a generator expression that captures it/)
    return t


def in_loop_direct(xs: list[int32], big: bool) -> int32:
    t = 0
    # free function: the body pops the iterated capture directly
    for v in xs:
        t += v + sum(1 for _ in range(1) if big and xs.pop() > 0)  # tpyc: warning(/Borrowed container 'xs' is mutated by a generator expression that captures it/)
    return t


def in_loop_late(xs: list[int32], big: bool) -> int32:
    t = 0
    # a callee defined below: the verdict waits for its facts
    for v in xs:
        t += v + sum(1 for _ in range(1) if big and grow_late(xs))  # tpyc: warning(/Borrowed container 'xs' is mutated by a generator expression that captures it/)
    return t


def grow_late(ys: list[int32]) -> bool:
    ys.append(8)
    return True


def element_loan(rs: list[Rec], big: bool) -> int32:
    first = rs[0]
    # an element borrow is live across the genexpr that pops its container
    n = sum(1 for _ in range(1) if big and rs.pop().k > 0)  # tpyc: warning(/Borrowed container 'rs' is mutated by a generator expression that captures it/)
    return first.k + n


class Runner:
    base: int32

    def __init__(self) -> None:
        self.base = 100

    def run(self, xs: list[int32], big: bool) -> int32:
        t = self.base
        # method
        for v in xs:
            t += v + sum(1 for _ in range(1) if big and grow(xs))  # tpyc: warning(/Borrowed container 'xs' is mutated by a generator expression that captures it/)
        return t


def gen_body(xs: list[int32], big: bool) -> Iterator[int32]:
    # generator body
    for v in xs:
        yield v + sum(1 for _ in range(1) if big and grow(xs))  # tpyc: warning(/Borrowed container 'xs' is mutated by a generator expression that captures it/)


async def async_body(xs: list[int32], big: bool) -> int32:
    t = 0
    # async body
    for v in xs:
        t += v + sum(1 for _ in range(1) if big and grow(xs))  # tpyc: warning(/Borrowed container 'xs' is mutated by a generator expression that captures it/)
    return t


def view_demoted(r: Rec) -> str:
    lbl = r.name  # tpyc: type(str)
    # the body renames the captured record, so `lbl` must own its text
    n = sum(1 for _ in range(1) if rename(r))  # tpyc: ok
    return lbl + " " + str(n)


def view_demoted_direct(r: Rec) -> str:
    lbl = r.name  # tpyc: type(str)
    # the body renames the captured record through its own method
    n = sum(1 for _ in range(1) if r.set_name())  # tpyc: ok
    return lbl + " " + str(n)


def in_comprehension(xs: list[int32], big: bool) -> int32:
    # a genexpr inside a comprehension over the same source: the
    # comprehension's loan is live while the genexpr body grows it
    return len([sum(1 for _ in range(1) if big and grow(xs)) for v in xs])  # tpyc: warning(/Borrowed container 'xs' is mutated by a generator expression that captures it/)


def in_nested_def(big: bool) -> int32:
    # nested def: the genexpr body grows the list its loop iterates (a local
    # of the nested def; a captured parameter is BUGS.md#nested-def-call-edge-capture-lost)
    def inner() -> int32:
        # annotated: an unannotated literal here settles as an Array, which
        # `grow` cannot take
        ys: list[int32] = [1, 2]
        t = 0
        for v in ys:
            t += v + sum(1 for _ in range(1) if big and grow(ys))  # tpyc: warning(/Borrowed container 'ys' is mutated by a generator expression that captures it/)
        return t
    return inner()


def src_nested_def(big: bool) -> int32:
    # nested def: the genexpr grows its own source
    def inner() -> int32:
        ys: list[int32] = [1, 2]
        return sum(v for v in ys if big and grow(ys))  # tpyc: warning(/Passing borrowed container 'ys' to non-readonly parameter 'ys'/)
    return inner()


class Counted:
    n: int32

    def __init__(self, xs: list[int32], big: bool) -> None:
        self.n = 0
        # constructor: a loop's loan is live while the genexpr grows it
        for v in xs:
            self.n += v + sum(1 for _ in range(1) if big and grow(xs))  # tpyc: warning(/Borrowed container 'xs' is mutated by a generator expression that captures it/)


def view_kept(r: Rec, xs: list[int32]) -> str:
    lbl = r.name  # tpyc: type(str)
    # the body only reads the captured record; `lbl` owns a copy all the same
    # (a field read never lends), the same form as view_demoted
    n = sum(1 for x in xs if x > r.k)  # tpyc: ok
    return lbl + " " + str(n)


def other_capture_grows(xs: list[int32]) -> int32:
    seen: set[int32] = set()
    t = 0
    # the body grows a capture nothing holds a loan on
    for v in xs:
        t += sum(1 for _ in range(1) if v not in seen and add_to(seen, v))  # tpyc: ok
    return t * 10 + len(seen)


def after_loop(xs: list[int32]) -> int32:
    t = 0
    for v in xs:
        t += v
    # the loop's loan has ended: growing the capture is fine
    t += sum(1 for _ in range(1) if grow(xs))  # tpyc: ok
    return t * 10 + len(xs)


class Bag:
    items: list[int32]
    other: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2, 3]
        self.other = [10, 20]

    def push(self) -> bool:
        self.items.append(9)
        return True

    def drain(self) -> int32:
        # method: the body pops a field of the captured self
        return sum(v + self.items.pop() for v in self.other)  # tpyc: ok

    def via_callee(self) -> int32:
        # method: a field of the captured self handed to a mutating callee
        return sum(v for v in self.other if grow(self.items))  # tpyc: ok

    def via_method(self) -> int32:
        # method: a mutating method called on the captured self
        return sum(v for v in self.other if self.push())  # tpyc: ok

    def src_field(self, big: bool) -> int32:
        # method: the body pops the field the genexpr iterates
        return sum(v + (self.items.pop() if big else 0) for v in self.items)  # tpyc: warning(/Mutation of 'self.items' while iterating over it \('pop' invalidates the iterator\)/)


def free_self(self: Bag) -> int32:
    # a free function whose parameter is named `self`: not a receiver either
    return sum(v + self.items.pop() for v in [100])  # tpyc: ok


def src_pop(xs: list[int32], big: bool) -> int32:
    # own source: the element pops the list being iterated
    return sum(v + (xs.pop() if big else 0) for v in xs)  # tpyc: warning(/Mutation of 'xs' while iterating over it \('pop' invalidates the iterator\)/)


def src_callee(xs: list[int32], big: bool) -> int32:
    # own source: the condition hands it to a mutating callee
    return sum(v for v in xs if big and grow(xs))  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter 'ys'/)


def src_callee_late(xs: list[int32], big: bool) -> int32:
    # own source, through a callee defined below
    return sum(v for v in xs if big and grow_late(xs))  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter 'ys'/)


def src_dict_view(d: dict[int32, int32], big: bool) -> int32:
    # own source: a view of the dict the body pops
    return sum(v for v in d.values() if big and d.pop(v) > 0)  # tpyc: warning(/Mutation of 'd' while iterating over it \('pop' invalidates the iterator\)/)


def src_unpack(ps: list[tuple[int32, int32]], big: bool) -> int32:
    # own source, tuple-unpacking head
    return sum(a + b for a, b in ps if big and grow_pairs(ps))  # tpyc: warning(/Passing borrowed container 'ps' to non-readonly parameter 'qs'/)


def src_for_over(xs: list[int32], big: bool) -> int32:
    t = 0
    # own source, grown by the frame while a `for` pulls from it
    for v in (x for x in xs if big and grow(xs)):  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter 'ys'/)
        t += v
    return t


def src_gen_body(xs: list[int32], big: bool) -> Iterator[int32]:
    # own source, genexpr inside a generator body
    yield sum(v for v in xs if big and grow(xs))  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter 'ys'/)


async def src_async_body(xs: list[int32], big: bool) -> int32:
    # own source, genexpr inside an async body
    return sum(v for v in xs if big and grow(xs))  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter 'ys'/)


def src_range(xs: list[int32]) -> int32:
    # a range source iterates no container: growing the list is fine
    n = sum(i for i in range(len(xs)) if grow(xs))  # tpyc: ok
    return n * 10 + len(xs)


def src_other_capture(xs: list[int32]) -> int32:
    seen: set[int32] = set()
    # the body grows a capture that is not the source
    n = sum(x for x in xs if x not in seen and add_to(seen, x))  # tpyc: ok
    return n * 10 + len(seen)


def src_reads(xs: list[int32]) -> int32:
    # the body only reads its source through the capture
    return sum(v for v in xs if len(xs) > 1)  # tpyc: ok


class Tagged:
    tag: str
    items: list[int32]
    n: int32

    def __init__(self) -> None:
        self.tag = "the original tag, long enough to live on the heap"
        self.items = [1, 2]
        self.n = 0
        # constructor: the body grows a field through a method on self
        self.n = sum(1 for _ in range(2) if self.push())  # tpyc: ok

    def __iter__(self) -> Iterator[int32]:
        for v in self.items:
            yield v

    def push(self) -> bool:
        self.items.append(9)
        return True

    def retag(self, x: int32) -> int32:
        self.tag = "a replacement long enough to leave the small-string buffer"
        return x

    def view_via_method(self, xs: list[int32]) -> str:
        v = self.tag  # tpyc: type(str)
        # the body rewrites the read field only through `self.retag()`
        total = sum(self.retag(x) for x in xs)  # tpyc: ok
        return v + " " + str(total)

    def iter_self(self, big: bool) -> int32:
        t = 0
        # the loop iterates self; the body grows it through `self.push()`
        for v in self:
            t += v + sum(1 for _ in range(1) if big and self.push())  # tpyc: warning(/Borrowed container 'self' is mutated by a generator expression that captures it/)
        return t

    def push2(self) -> bool:
        return self.push()

    def iter_two_hop(self, big: bool) -> int32:
        t = 0
        # the body grows self two calls deep: `self.push2()` -> `self.push()`
        for v in self:
            t += v + sum(1 for _ in range(1) if big and self.push2())  # tpyc: warning(/Borrowed container 'self' is mutated by a generator expression that captures it/)
        return t

    def count_into(self, other: "Tagged") -> int32:
        # a nested def's own param named `self` shadows the receiver: pushing
        # through it leaves this method readonly
        def inner(self: Tagged) -> bool:
            return self.push()
        inner(other)  # tpyc: ok
        return len(self.items)

    def count_direct(self, other: "Tagged") -> int32:
        # the nested def's own `self` grows directly: this method stays readonly
        def inner(self: Tagged) -> None:
            self.items.append(1)
        inner(other)  # tpyc: ok
        return len(self.items)

    def view_other(self, r: Rec) -> str:
        lbl = r.name  # tpyc: type(str)
        # the body grows self and only reads `r`; `lbl` owns a copy all the same
        n = sum(1 for _ in range(1) if self.push() and r.k >= 0)  # tpyc: ok
        return lbl + " " + str(n)

    def rec(self, k: int32) -> int32:
        # recursion through the body's `self.rec()` and `self.push()`
        return sum(v for v in [1, 2] if k > 0 and self.rec(k - 1) >= 0 and self.push())  # tpyc: ok


def helper_self(self: Tagged, k: int32) -> bool:
    return self.push()


def helper_second(b: Tagged, self: Tagged) -> bool:
    return self.push()


def free_self_walk(self: Tagged, big: bool) -> int32:
    t = 0
    # a free function iterating its param named `self` while the body grows
    # it: the param is a mutable borrow (`Tagged& self`)
    for v in self:
        t += v + sum(1 for _ in range(1) if big and self.push())  # tpyc: warning(/Borrowed container 'self' is mutated by a generator expression that captures it/)
    return t


def free_self_param(b: Tagged, c: Tagged, big: bool) -> int32:
    t = 0
    for v in b:
        if big:
            # a free function's param named `self` is a param: growing it
            # through `self.push()` reaches the argument
            helper_self(b, 1)  # tpyc: warning(/Passing borrowed container 'b' to non-readonly parameter 'self'/)
            # the grown `self` is the SECOND param here, not `b`
            helper_second(b, c)  # tpyc: ok
        t += v
    return t


def global_src_pop(big: bool) -> int32:
    # own source is a module global the body reads directly
    return sum(v + (gsrc.pop() if big else 0) for v in gsrc)  # tpyc: warning(/Mutation of 'gsrc' while iterating over it \('pop' invalidates the iterator\)/)


def global_src_callee(big: bool) -> int32:
    # own source is a module global, grown through a callee
    return sum(v for v in gsrc if big and grow(gsrc))  # tpyc: warning(/Passing borrowed container 'gsrc' to non-readonly parameter 'ys'/)


def global_other_grows() -> int32:
    # a global source; the body grows a DIFFERENT global
    return sum(v for v in gsrc if grow(gother))  # tpyc: ok


def main() -> None:
    print("in_loop_callee:", in_loop_callee([1, 2], False))
    print("in_loop_direct:", in_loop_direct([1, 2], False))
    print("in_loop_late:", in_loop_late([1, 2], False))
    print("element_loan:", element_loan([Rec("a", 3), Rec("b", 4)], False))
    print("method:", Runner().run([1, 2], False))
    print("gen_body:", sum(gen_body([1, 2], False)))
    print("async_body:", asyncio.run(async_body([1, 2], False)))
    print("view_demoted:", view_demoted(Rec("the original label, long enough to be on the heap", 1)))
    print("view_demoted_direct:", view_demoted_direct(Rec("the original label, long enough to be on the heap", 1)))
    print("in_comprehension:", in_comprehension([1, 2], False))
    print("in_nested_def:", in_nested_def(False))
    print("src_nested_def:", src_nested_def(False))
    cl = [1, 2]
    print("ctor_loan:", Counted(cl, False).n)
    print("view_kept:", view_kept(Rec("kept", 1), [1, 2, 3]))
    print("other_capture_grows:", other_capture_grows([1, 2, 1, 3]))
    print("after_loop:", after_loop([1, 2]))
    bag = Bag()
    print("self_drain:", bag.drain(), bag.items)
    print("self_via_callee:", bag.via_callee(), bag.items)
    print("self_via_method:", bag.via_method(), bag.items)
    print("free_self:", free_self(bag), bag.items)
    print("src_pop:", src_pop([1, 2], False))
    print("src_callee:", src_callee([1, 2], False))
    print("src_callee_late:", src_callee_late([1, 2], False))
    print("src_field:", Bag().src_field(False))
    print("src_dict_view:", src_dict_view({1: 10, 2: 20}, False))
    print("src_unpack:", src_unpack([(1, 2), (3, 4)], False))
    print("src_for_over:", src_for_over([1, 2], False))
    print("src_gen_body:", sum(src_gen_body([1, 2], False)))
    print("src_async_body:", asyncio.run(src_async_body([1, 2], False)))
    print("src_range:", src_range([5, 6]))
    print("src_other_capture:", src_other_capture([1, 2, 1, 3]))
    print("src_reads:", src_reads([1, 2]))
    tg = Tagged()
    print("ctor_push:", tg.n, tg.items)
    print("view_via_method:", tg.view_via_method([1, 2]))
    print("iter_self:", tg.iter_self(False))
    print("rec:", tg.rec(2), tg.items)
    print("iter_two_hop:", tg.iter_two_hop(False))
    print("view_other:", tg.view_other(Rec("a label long enough to live on the heap", 1)), tg.items)
    print("free_self_walk:", free_self_walk(Tagged(), False))
    print("free_self_param:", free_self_param(Tagged(), Tagged(), False))
    other = Tagged()
    print("count_into:", tg.count_into(other), len(other.items))
    print("count_direct:", tg.count_direct(other), len(other.items))
    print("global_src_pop:", global_src_pop(False))
    print("global_src_callee:", global_src_callee(False))
    print("global_other_grows:", global_other_grows(), gother)


big_module = False
gl = [1, 2, 3]
gt = 0
# module level
for gv in gl:
    gt += gv + sum(1 for _ in range(1) if big_module and grow(gl))  # tpyc: warning(/Borrowed container 'gl' is mutated by a generator expression that captures it/)
print("module:", gt)
# module level, own source
print("module_src:", sum(v for v in gl if big_module and grow(gl)))  # tpyc: warning(/Passing borrowed container 'gl' to non-readonly parameter 'ys'/)

main()
