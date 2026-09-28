# A receiver-less spelled call (module-qualified, static, classmethod, super)
# lowers its arguments exactly like the from-import spelling of the same callee.
import asyncio
from typing import Iterator

import pets
from pets import Cat, Dog, K, gen_sizes, gen_tags, seq
from tplib import Rc
from tpy import Own, dispatch, int32, readonly


class Base:
    def code(self, pet: Cat | Dog) -> int:
        if isinstance(pet, Dog):
            return pet.age
        return pet.lives


class Sub(Base):
    def code(self, pet: Cat | Dog) -> int:
        # super: a union rvalue at the parent's read-only slot.
        return super().code(Dog(4)) + 100  # tpyc: ok


class GBox[T]:
    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)

    @dispatch
    def ov(self, x: str) -> int:
        return len(x)

    @dispatch
    def ov(self, x: bool) -> int:
        return 7


def tick(tag: str, n: int) -> int:
    print(tag, n)
    return n


class Holder:
    @staticmethod
    def take_list(xs: list[int]) -> int:
        xs.append(99)
        return len(xs)


def mk() -> Own[list[int]]:
    return [1, 2]


def wrap_b(b: bytes) -> Own[Rc[bytes]]:
    # A `bytes` parameter at a static `Own[bytes]` slot copies in.
    return Rc.new(b)  # tpyc: ok


def gen_codes() -> Iterator[int]:
    # generator: the union rvalue inside a resumable body.
    yield pets.code(Dog(6))  # tpyc: ok


async def async_code() -> int:
    # async: the union rvalue inside a coroutine body.
    n = pets.code(Dog(7))  # tpyc: ok
    return n


def cond(c: bool) -> int:
    # conditional operand: the list-repeat temp at a qualified slot.
    return 0 if c else pets.ls([0] * 3)  # tpyc: ok


def main() -> None:
    # module: a union rvalue at a read-only and at a mutated slot.
    print("module_const", pets.code(Dog(4)))  # tpyc: ok
    pets.bump(Dog(4))  # tpyc: ok
    pet: Cat | Dog = Dog(1)
    # module: the lvalue at the mutated slot is the caller's object.
    pets.bump(pet)  # tpyc: ok
    print("module_mut", pets.code(pet))
    print("static", K.static_code(Dog(2)))  # tpyc: ok
    print("classmethod", K.cls_code(Dog(3)))  # tpyc: ok
    print("super", Sub().code(Cat(1)))
    # list repeat at a read-only list slot.
    print("list_repeat", pets.ls([0] * 3))  # tpyc: ok
    # container literals at a read-only and a mutated slot (both hoisted).
    print("literal_const", pets.ls([1, 2]), pets.dct({"a": 1}))  # tpyc: ok
    print("literal_mut", pets.ls_mut([1, 2]))  # tpyc: ok
    # a non-empty literal at a declared readonly parameter hoists too.
    print("readonly_literal", pets.total([1, 2, 3]))  # tpyc: ok
    print("generator", list(gen_codes()))
    print("async", asyncio.run(async_code()))
    print("cond", cond(True), cond(False))
    # a container-returning call at a static method's mutated slot.
    print("call_rvalue", Holder.take_list(mk()))  # tpyc: ok
    # a tuple literal at a qualified Optional[tuple] slot.
    print("tuple_literal", pets.request("http://x", ("user", "pw")))  # tpyc: ok
    print("bytes_own", str(wrap_b(b"xy")))
    # @dispatch: a str literal takes the `str` overload, never `bool`.
    print("dispatch", pets.ov("abc"), K.sov("abc"))  # tpyc: ok
    # @dispatch on a GENERIC record's method: the same `str` pick.
    print("generic_dispatch", GBox(1).ov("abc"))  # tpyc: ok
    # argument order with the literal FIRST: its hoisted element runs before
    # the later argument, as in CPython -- in both spellings (a literal in a
    # later position runs first too: BUGS.md#subexpression-right-to-left-eval).
    n = seq([tick("order_free", 1)], tick("order_free", 2))  # tpyc: ok
    print("order_free", n)
    n = pets.seq([tick("order_qualified", 1)],
                 tick("order_qualified", 2))  # tpyc: ok
    print("order_qualified", n)
    # generator factory: a str / bytes literal at a view slot is static
    # storage, so it passes bare (no owned temp) and the frame, drained a
    # statement later, still reads it -- in both spellings.
    tq = pets.gen_tags("ab")  # tpyc: ok
    tf = gen_tags("cd")  # tpyc: ok
    bq = pets.gen_sizes(b"xyz")  # tpyc: ok
    bf = gen_sizes(b"wxyz")  # tpyc: ok
    print("literal_frame", list(tq), list(tf), list(bq), list(bf))

    # nested def: a list literal at a declared readonly parameter hoists.
    def inner(xs: readonly[list[int32]]) -> int32:
        total = 0
        for x in xs:
            total += x
        return total

    print("nested_readonly_literal", inner([1, 2, 3]))  # tpyc: ok


main()
