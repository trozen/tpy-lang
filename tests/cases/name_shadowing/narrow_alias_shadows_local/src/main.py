# An isinstance narrowing declares an extraction alias spelled `__<var>`. When
# that name is already taken -- by a binding anywhere in the body at any scope,
# by a module global the body only reads, or by another narrowing's alias live
# in an enclosing branch -- the alias must be bumped past it, or it shadows (or
# redeclares) what was there. A resumable frame re-establishes the alias at each
# resume case under the SAME ladder, so a narrowing that stays live across a
# suspension keeps one spelling on both sides. Each section reads BOTH the
# narrowed subject and the same-spelled binding inside the branch, so a shadow
# changes the printed value; sections are named after the position they cover.
import asyncio
from typing import Iterator, Protocol

from tpy import Fn, dynamic, int32


class Dog:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def sound(self) -> str:
        return "woof"


class Cat:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def sound(self) -> str:
        return "meow"


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Bird(Pet):
    def name(self) -> str:
        return "bird"

    def chirp(self) -> str:
        return "tweet"


class Fish(Pet):
    def name(self) -> str:
        return "fish"


# module level: the alias avoids a top-level binding of the spelling
mod_a: Dog | Cat = Cat(5)
__mod_a = 12
if isinstance(mod_a, Cat):  # tpyc: ok
    print("module", mod_a.sound(), __mod_a)


# free function: the alias bumps past the plain local
def plain(a: Dog | Cat) -> None:
    __a = 7
    if isinstance(a, Cat):  # tpyc: ok
        print("plain", a.sound(), __a)


# free function: the bumped spelling is a user binding too, so it bumps again
def bumped_twice(a: Dog | Cat) -> None:
    __a = 7
    __a_narrowed = 11
    if isinstance(a, Cat):  # tpyc: ok
        print("bumped_twice", a.sound(), __a, __a_narrowed)


# two subjects whose spellings collide only AFTER a bump: `a` takes
# `__a_narrowed` because the body binds `__a`, and that is also the plain
# spelling for a subject named `a_narrowed`
def cross(a: Dog | Cat, a_narrowed: Dog | Cat) -> None:
    __a = 7
    if isinstance(a, Cat):  # tpyc: ok
        if isinstance(a_narrowed, Dog):  # tpyc: ok
            print("cross", a.sound(), a_narrowed.sound(), __a)


# the same cross-subject collision on the multi-var narrow, whose aliases
# share one C++ scope
def multi_cross(a: Dog | Cat, a_narrowed: Dog | Cat) -> None:
    __a = 8
    if isinstance(a, Cat) and isinstance(a_narrowed, Dog):  # tpyc: ok
        print("multi_cross", a.sound(), a_narrowed.sound(), __a)


# a module global of the alias spelling: the body only READS it, so nothing
# about the body's own bindings can report it
__gsub = 12


def global_read(gsub: Dog | Cat) -> None:
    if isinstance(gsub, Cat):  # tpyc: ok
        print("global_read", gsub.sound(), __gsub)


# a `match` arm capture of the alias spelling -- arm-scoped, like an
# `except ... as` binding
def match_capture(a: Dog | Cat, other: Dog | Cat) -> None:
    if isinstance(a, Cat):  # tpyc: ok
        match other:
            case Cat() as __a:
                print("match_capture", a.sound(), __a.n)
            case _:
                print("match_capture other")


# a lambda param of the alias spelling -- lambda-scoped; the closure captures
# the narrowed subject, so its capture entry must name the ALIAS. The
# non-escaping (Fn) lane binds it by reference: the closure mutates the
# subject and the post-call read sees it, where a snapshot would print 1. The
# escaping (Callable) lane over a reference-typed narrowed subject rejects
# (BUGS.md#escaping-capture-of-narrowed-subject)
def bump(c: Cat, k: int32) -> int32:
    c.n += k
    return c.n


def call_cat(f: Fn[[Cat], int32], c: Cat) -> int32:
    return f(c)


def lambda_param(a: Dog | Cat) -> None:
    if isinstance(a, Cat):  # tpyc: ok
        n = call_cat(lambda __a: bump(a, __a.n), Cat(100))
        print("lambda_param", a.sound(), n, a.n)


# multi-var narrow: one alias per subject, each clear of the user's bindings
def multi(a: Dog | Cat, b: Dog | Cat) -> None:
    __a = 1
    __b = 2
    if isinstance(a, Cat) and isinstance(b, Dog):  # tpyc: ok
        print("multi", a.sound(), b.sound(), __a, __b)


# persistent alias after an `assert`: same C++ scope as the local, so a
# collision is an outright redeclaration
def asserted(a: Dog | Cat) -> None:
    __a = 4
    assert isinstance(a, Cat)  # tpyc: ok
    print("asserted", a.sound(), __a)


# persistent alias from the complement of an early-returning `if`
def complement(a: Dog | Cat) -> None:
    __a = 3
    if isinstance(a, Dog):
        return
    print("complement", a.sound(), __a)  # tpyc: ok


# the colliding local is declared INSIDE the branch, after the alias
def inner_decl(a: Dog | Cat) -> None:
    if isinstance(a, Cat):  # tpyc: ok
        __a = 6
        print("inner_decl", a.sound(), __a)


# a comprehension loop var of the alias spelling: its own inner scope, so it
# would shadow the alias for the reads inside the comprehension
def comprehension(a: Dog | Cat) -> None:
    if isinstance(a, Cat):  # tpyc: ok
        xs = [a.n + __a for __a in [1, 2]]
        print("comprehension", a.sound(), xs)


# a nested def of the alias spelling
def nested(a: Dog | Cat) -> None:
    if isinstance(a, Cat):  # tpyc: ok
        def __a(k: int32) -> int32:
            return k + 1
        print("nested", a.sound(), __a(4))


# polymorphic narrow: the subject reads through the if-init cast POINTER, a
# second synthesized name that must clear the body's bindings too
def poly(p: Pet) -> None:
    __p_ptr = 13
    if isinstance(p, Bird):  # tpyc: ok
        print("poly", p.chirp(), __p_ptr)
    else:
        print("poly other", p.name(), __p_ptr)


# generator: the local is a frame field named exactly like the alias
def gen(a: Dog | Cat) -> Iterator[int32]:
    __a = 5
    yield 0
    if isinstance(a, Cat):  # tpyc: ok
        print("gen", a.sound(), __a)


# generator: the narrowing is live ACROSS the yield, so the resume case
# re-establishes the alias under the same bumped spelling
def gen_across(a: Dog | Cat) -> Iterator[int32]:
    __a = 8
    if isinstance(a, Cat):  # tpyc: ok
        yield 1
        print("gen_across", a.sound(), __a)


# generator: the cross-subject collision inside a frame, where the bumped
# spelling is also what the frame's own field set would take
def gen_cross(a: Dog | Cat, a_narrowed: Dog | Cat) -> Iterator[int32]:
    __a = 5
    yield 0
    if isinstance(a, Cat):  # tpyc: ok
        if isinstance(a_narrowed, Dog):  # tpyc: ok
            print("gen_cross", a.sound(), a_narrowed.sound(), __a)


# generator: an `assert` narrowing at a flat walk position
def gen_assert(a: Dog | Cat) -> Iterator[int32]:
    __a = 2
    yield 3
    assert isinstance(a, Cat)  # tpyc: ok
    print("gen_assert", a.sound(), __a)


# generator: the alias spelling AND its bump are both frame fields, so the
# ladder has to bump twice for a narrowing re-established at a resume case
def gen_bumped_frame(a: Dog | Cat) -> Iterator[int32]:
    __a = 5
    __a_narrowed = 6
    if isinstance(a, Cat):  # tpyc: ok
        yield 1
        print("gen_bumped_frame", a.sound(), __a, __a_narrowed)


# generator: two subjects whose spellings collide after a bump, BOTH live
# across the yield -- one resume case declares both, so they must differ
def gen_resume_cross(a: Dog | Cat, a_narrowed: Dog | Cat) -> Iterator[int32]:
    __a = 5
    assert isinstance(a, Cat)  # tpyc: ok
    assert isinstance(a_narrowed, Dog)  # tpyc: ok
    yield 1
    a.n += 1
    print("gen_resume_cross", a.sound(), a_narrowed.sound(), __a)


# generator: an unrelated param spelled like the BUMPED alias and never
# narrowed -- it is no narrowing subject, so it must not block the bump
def gen_sibling(a: Dog | Cat, a_narrowed: int32) -> Iterator[int32]:
    __a = 5
    yield 0
    if isinstance(a, Cat):  # tpyc: ok
        a.n += 1
        yield a.n + __a + a_narrowed


# async: the same collision across a suspension point
async def coro(a: Dog | Cat) -> None:
    __a = 9
    await asyncio.sleep(0)
    if isinstance(a, Cat):  # tpyc: ok
        print("coro", a.sound(), __a)


# async: the complement alias after a suspension
async def coro_complement(a: Dog | Cat) -> str:
    __a = 1
    await asyncio.sleep(0)
    if isinstance(a, Cat):
        return "cat"
    return a.sound() + str(__a)  # tpyc: ok


# async: a module global of the alias spelling while the narrowing stays live
# across the await -- the resume case's re-established alias must bump past the
# global exactly as the pre-suspension read does
__acache = 10


async def coro_global(acache: Dog | Cat) -> str:
    if isinstance(acache, Cat):  # tpyc: ok
        before = acache.n
        await asyncio.sleep(0)
        acache.n += __acache
        return ("coro_global " + acache.sound() + " " + str(before) + " "
                + str(acache.n))
    return "coro_global dog"


def main() -> None:
    plain(Cat(1))
    bumped_twice(Cat(1))
    cross(Cat(1), Dog(2))
    multi_cross(Cat(1), Dog(2))
    global_read(Cat(1))
    match_capture(Cat(1), Cat(100))
    lambda_param(Cat(1))
    multi(Cat(1), Dog(2))
    asserted(Cat(1))
    complement(Cat(1))
    inner_decl(Cat(1))
    comprehension(Cat(10))
    nested(Cat(1))
    poly(Bird())
    poly(Fish())
    for v in gen(Cat(2)):
        print("gen yield", v)
    for v in gen_across(Cat(2)):
        print("gen_across yield", v)
    for v in gen_cross(Cat(2), Dog(3)):
        print("gen_cross yield", v)
    for v in gen_assert(Cat(2)):
        print("gen_assert yield", v)
    for v in gen_bumped_frame(Cat(2)):
        print("gen_bumped_frame yield", v)
    rc = Cat(2)
    for v in gen_resume_cross(rc, Dog(3)):
        print("gen_resume_cross yield", v)
    print("gen_resume_cross after", rc.n)
    gs = Cat(4)
    for v in gen_sibling(gs, 7):
        print("gen_sibling yield", v)
    print("gen_sibling after", gs.n)
    asyncio.run(coro(Cat(3)))
    print("coro_complement", asyncio.run(coro_complement(Dog(4))))
    cg = Cat(3)
    print(asyncio.run(coro_global(cg)))
    print("coro_global after", cg.n)


main()
