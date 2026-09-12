# A mutation through a `match`-arm capture is credited to the matched
# subject, exactly as the `isinstance` twin's is: the param stays mutable,
# the enclosing method loses its inferred readonly, and the write reaches the
# caller's object. Every section mutates AFTER the boundary and reads the
# change back through the caller, so a silent copy shows up as a wrong value.
# The three inverse sections at the end pin what must NOT move.
import asyncio
from dataclasses import dataclass
from typing import Iterator, Protocol

from tpy import int32, Own, dynamic


class Cat:
    hunger: int32
    tags: list[str]

    def __init__(self, hunger: int32) -> None:
        self.hunger = hunger
        self.tags = []


class Dog:
    bones: int32
    tags: list[str]

    def __init__(self, bones: int32) -> None:
        self.bones = bones
        self.tags = []


class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


# free function, union param, `as` capture
def free_union(a: Cat | Dog) -> None:
    match a:
        case Cat() as c:
            c.hunger -= 1  # tpyc: ok
        case Dog() as d:
            d.bones += 1


# free function, record param, bare-name capture
def free_record(b: Counter) -> None:
    match b:
        case q:
            q.n = 99  # tpyc: ok


# or-pattern alternatives binding one field sub-capture; the mutation is
# STRUCTURAL (append), which reaches the param through the same alias
def or_sub_capture(a: Cat | Dog) -> None:
    match a:
        case Cat(tags=ts) | Dog(tags=ts):
            ts.append("x")  # tpyc: ok
        case _:
            pass


# Optional subject: the capture binds the narrowed payload
def optional_subject(x: Counter | None) -> None:
    match x:
        case None:
            pass
        case v:
            v.n += 1  # tpyc: ok


def add_one(c: Counter) -> None:
    c.n += 1


# the capture is handed to a MUTATING callee rather than written directly
def via_callee(u: Counter | Cat) -> None:
    match u:
        case Counter() as got:
            add_one(got)  # tpyc: ok
        case Cat() as c:
            c.hunger += 1


class Bag:
    c: Counter

    def __init__(self, n: int32) -> None:
        self.c = Counter(n)

    # method, `self.field` subject: the write is the method's only mutation,
    # so nothing else can demote its inferred readonly
    def bump(self) -> None:
        match self.c:
            case Counter() as q:
                q.n += 1  # tpyc: ok

    def value(self) -> int32:
        return self.c.n


# a capture RE-SEATED by a nested match holds one loan per subject, so the
# write has to credit both params, not whichever loan came first
def reseated(h: Bag, g: Bag) -> None:
    match h:
        case Bag(c=q):
            match g:
                case Bag(c=q):
                    q.n += 7  # tpyc: ok


# the same re-seated capture reaching its mutation through a CALLEE rather
# than a direct write: the call-edge attribution owes every loan an edge
def reseated_callee(h: Bag, g: Bag) -> None:
    match h:
        case Bag(c=q):
            match g:
                case Bag(c=q):
                    add_one(q)  # tpyc: ok


class Owner:
    mine: Bag

    def __init__(self, n: int32) -> None:
        self.mine = Bag(n)

    # the capture is re-seated between a `self.field` subject and a PARAM
    # subject, so the write owes BOTH: the method loses its inferred readonly
    # (the snapshot pins the missing const qualifier) and `other` stays
    # mutable. A vararg section would belong here too, but passing a capture
    # as a vararg does not build at all
    # (BUGS.md#vararg-of-pointer-capture-takes-address).
    def touch(self, other: Bag) -> None:
        match self.mine:
            case Bag(c=q):
                match other:
                    case Bag(c=q):
                        q.n += 4  # tpyc: ok

    def value(self) -> int32:
        return self.mine.c.n


# SUBSCRIPT-rooted subject: the loan is rooted at the container, so the write
# through the capture keeps the container param mutable
def subscript_subject(xs: list[Counter], i: int32) -> None:
    match xs[i]:
        case Counter() as e:
            e.n += 3  # tpyc: ok


@dataclass
class Slot:
    n: int32


@dataclass
class Pair:
    left: Slot
    right: Slot


# POSITIONAL class-pattern sub-captures bind the same way the keyword ones do
def positional_sub_capture(p: Pair) -> None:
    match p:
        case Pair(a, b):
            a.n += 1  # tpyc: ok
            b.n += 2


class UnionHolder:
    payload: Counter | Cat

    def __init__(self, n: int32) -> None:
        self.payload = Counter(n)

    # the literal BUGS shape: a UNION-typed `self.field` matched with an
    # `as`-capture whose write is the method's only mutation, so it alone
    # decides the method's const qualifier
    def bump(self, v: int32) -> None:
        match self.payload:
            case Counter() as c:
                c.n = v  # tpyc: ok
            case Cat() as k:
                k.hunger = v

    def value(self) -> int32:
        match self.payload:
            case Counter() as c:
                return c.n
            case Cat() as k:
                return k.hunger


@dynamic
class Pet(Protocol):
    def bump(self) -> None: ...


class Kitty(Pet):
    lives: int32

    def __init__(self, lives: int32) -> None:
        self.lives = lives

    def bump(self) -> None:
        self.lives += 1


# @dynamic-protocol subject: the arm dispatches by runtime type
def poly_subject(p: Pet) -> None:
    match p:
        case Kitty() as seen:
            seen.bump()  # tpyc: ok
        case _:
            pass


# generator body. The suspension is OUTSIDE the match: an arm that carries one
# turns the capture into a frame field, which is an emplaced COPY, so the
# mutation would stop at that copy instead of reaching the caller
# (BUGS.md#resumable-match-capture-frame-emplace-copies)
def gen_body(a: Counter | Cat) -> Iterator[int32]:
    match a:
        case Counter() as c:
            c.n += 1  # tpyc: ok
        case Cat() as k:
            k.hunger += 1
    yield 1


# async body: the same union param at the coroutine's own parameter slot
async def async_body(a: Counter | Cat) -> int32:
    match a:
        case Counter() as c:
            c.n += 1  # tpyc: ok
            return c.n
        case Cat() as k:
            return k.hunger
    return 0


# inverse: an arm that only READS keeps the param's non-mutating verdict --
# the snapshot pins the `const std::variant<...>` this section must keep
def read_only(a: Counter | Cat) -> int32:
    match a:
        case Counter() as c:
            return c.n
        case Cat() as k:
            return k.hunger


def clone_of(c: Counter) -> Own[Counter]:
    return Counter(c.n)


# inverse: an RVALUE subject owns a temporary, so the write reaches no caller
# storage -- `c` must stay a non-mutating (const) param
def rvalue_subject(c: Counter) -> int32:
    match clone_of(c):
        case Counter() as q:
            q.n += 1
            return q.n
    return 0


# inverse: a free-copy scalar capture is a durable COPY, so rebinding and
# writing it leaves the subject alone
def scalar_capture(n: int32) -> int32:
    match n:
        case v:
            v = v + 1
            return v
    return 0


# LOCAL subject rather than a param: the local is already mutable, so the
# section pins that the capture write still lands on it
def local_subject() -> int32:
    loc = Counter(0)
    match loc:
        case Counter() as lc:
            lc.n += 5  # tpyc: ok
    return loc.n


def main() -> None:
    cat = Cat(5)
    free_union(cat)
    print("free_union:", cat.hunger)

    ctr = Counter(1)
    free_record(ctr)
    print("free_record:", ctr.n)

    dog = Dog(2)
    or_sub_capture(dog)
    print("or_sub_capture:", len(dog.tags), dog.tags[0])

    opt = Counter(10)
    optional_subject(opt)
    print("optional_subject:", opt.n)

    callee = Counter(20)
    via_callee(callee)
    print("via_callee:", callee.n)

    bag = Bag(30)
    bag.bump()
    print("bump:", bag.value())

    left = Bag(40)
    right = Bag(50)
    reseated(left, right)
    print("reseated:", left.value(), right.value())

    cl = Bag(40)
    cr = Bag(50)
    reseated_callee(cl, cr)
    print("reseated_callee:", cl.value(), cr.value())

    own = Owner(1)
    other = Bag(10)
    own.touch(other)
    print("self_and_param_reseat:", own.value(), other.value())

    xs = [Counter(1), Counter(2)]
    subscript_subject(xs, 1)
    print("subscript_subject:", xs[0].n, xs[1].n)

    pair = Pair(Slot(10), Slot(10))
    positional_sub_capture(pair)
    print("positional_sub_capture:", pair.left.n, pair.right.n)

    uh = UnionHolder(0)
    uh.bump(42)
    print("union_field_method:", uh.value())

    kitty = Kitty(9)
    poly_subject(kitty)
    print("poly_subject:", kitty.lives)

    gen_ctr = Counter(60)
    for got in gen_body(gen_ctr):
        print("gen_body:", got)
    print("gen_body after:", gen_ctr.n)


    async_ctr = Counter(70)
    print("async_body:", asyncio.run(async_body(async_ctr)))
    print("async_body after:", async_ctr.n)

    ro = Counter(80)
    print("read_only:", read_only(ro), ro.n)

    rv = Counter(90)
    print("rvalue_subject:", rvalue_subject(rv), rv.n)

    print("scalar_capture:", scalar_capture(100))

    print("local_subject:", local_subject())


main()
