# Generic generator functions: type-parameterized generators over Iterable[T].
# A generator's bare-`T` parameter renders `::tpy::borrow_frame_param_t<T>` --
# a reference at EVERY instantiation, because the peephole captures it by
# reference and that capture has to bind the call site's object. The `bump_each`
# section is what makes the REFERENCE half of that trait observable: the write
# the body makes through the slot reaches the caller's object, exactly as it
# does at the monomorphic twin. (What the consumer does to the YIELDED value
# does not come back -- BUGS.md#generic-generator-yields-open-t-by-value -- so
# no section here mutates one.) A `readonly[T]` slot keeps the reference and
# adds the const -- `const T&`, not the `readonly_form_t<T>` the sibling
# positions spell, which would be the VIEW at str and dangle.
from tpy import Int32, readonly
from typing import Iterable, Iterator, Protocol


class Appendable(Protocol):
    def append(self, v: Int32) -> None: ...


class Readable(Protocol):
    @readonly
    def size(self) -> Int32: ...


class Bin:
    total: Int32

    def __init__(self) -> None:
        self.total = 0

    def append(self, v: Int32) -> None:
        self.total += v

    @readonly
    def size(self) -> Int32:
        return self.total


def bump_each[T: Appendable](obj: T, count: Int32) -> Iterator[Int32]:
    # the bare-`T` slot, MUTATED through its bound's method
    obj.append(1)
    i: Int32 = 0
    while i < count:
        yield i
        i += 1


def size_each[T: Readable](obj: readonly[T], count: Int32) -> Iterator[Int32]:
    # the `readonly[T]` slot: `const T&` at every instantiation -- still a
    # reference (the capture rule), but const, so only a @readonly method is
    # callable through it and `obj.append(1)` here would not compile
    i: Int32 = 0
    while i < count:
        yield obj.size() + i
        i += 1


class Sized(Protocol):
    @readonly
    def __len__(self) -> Int32: ...


def len_each[T: Sized](obj: readonly[T], count: Int32) -> Iterator[Int32]:
    # the same slot at a VALUE instantiation: `const T&` is a reference at str
    # too, never the view -- which is what makes the peephole's `[&obj]`
    # capture bind the caller's object rather than a parameter that dies with
    # the factory. Fed a NAMED local, because an rvalue here is not hoisted
    # into a caller temp (BUGS.md#readonly-tparam-slot-skips-arg-temp).
    i: Int32 = 0
    while i < count:
        yield len(obj) + i
        i += 1

def repeat[T](value: T, count: Int32) -> Iterator[T]:
    i: Int32 = 0
    while i < count:
        yield value
        i += 1

def enumerate[T](iterable: Iterable[T]) -> Iterator[tuple[Int32, T]]:
    i: Int32 = 0
    for item in iterable:
        yield (i, item)
        i += 1

def main() -> None:
    # Generic while-generator with value type
    for x in repeat(42, 3):
        print(x)

    # Generic while-generator with str
    for s in repeat("hi", 2):
        print(s)

    # Generic for-generator (enumerate) over list[str]
    words = ["hello", "world", "foo"]
    for i, w in enumerate(words):
        print(i, w)

    # enumerate over list[Int32]
    nums = [10, 20, 30]
    for i, n in enumerate(nums):
        print(i, n)

    # compose: enumerate directly over repeat (generator over generator)
    for i, s in enumerate(repeat("x", 3)):
        print(i, s)

    # a REFERENCE instantiation of a mutated bare-`T` slot
    b = Bin()
    for k in bump_each(b, 2):  # tpyc: ok
        print("bump", k)
    print("bump total", b.total)

    # the readonly sibling of that slot, at the same record
    for k in size_each(b, 2):  # tpyc: ok
        print("size", k)
    # and at str, whose slot is `const std::string&`, not the view
    word = "ro"
    for t in len_each(word, 2):  # tpyc: ok
        print("len", t)

main()
