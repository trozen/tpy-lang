# Generic generator functions: type-parameterized generators over Iterable[T].
# A generator's bare-`T` parameter takes the ordinary generic slot
# (`param_val_or_ref_t<T>`) and the frame holds `val_or_ref_t<T>`: a
# reference for a reference-typed instantiation, a copy for a value-typed one.
# The `bump_each` section is what makes the REFERENCE half observable: the
# write the body makes through the slot reaches the caller's object, exactly
# as it does at the monomorphic twin. (What the consumer does to the YIELDED
# value does not come back -- BUGS.md#generic-generator-yields-open-t-by-value
# -- so no section here mutates one.)
from tpy import int32, readonly
from typing import Iterable, Iterator, Protocol


class Appendable(Protocol):
    def append(self, v: int32) -> None: ...


class Readable(Protocol):
    @readonly
    def size(self) -> int32: ...


class Bin:
    total: int32

    def __init__(self) -> None:
        self.total = 0

    def append(self, v: int32) -> None:
        self.total += v

    @readonly
    def size(self) -> int32:
        return self.total


def bump_each[T: Appendable](obj: T, count: int32) -> Iterator[int32]:
    # the bare-`T` slot, MUTATED through its bound's method
    obj.append(1)
    i: int32 = 0
    while i < count:
        yield i
        i += 1


def size_each[T: Readable](obj: readonly[T], count: int32) -> Iterator[int32]:
    # the `readonly[T]` slot: the param is `readonly_form_t<T>` and the frame
    # member `val_or_cref_t<T>` -- a const reference at a reference
    # instantiation, a copy at a value one. Const either way, so only a
    # @readonly method is callable through it and `obj.append(1)` here would
    # not compile. The same classifier serves `async def`
    # (async/generic_async_free_func).
    i: int32 = 0
    while i < count:
        yield obj.size() + i
        i += 1


class Sized(Protocol):
    @readonly
    def __len__(self) -> int32: ...


def len_each[T: Sized](obj: readonly[T], count: int32) -> Iterator[int32]:
    # the same slot at a VALUE instantiation (str): the frame copies the
    # argument into its own member (`val_or_cref_t<T>`), which is what lets
    # an RVALUE source below survive -- the factory's `readonly_form_t<T>`
    # slot is only a view of the caller's temporary.
    i: int32 = 0
    while i < count:
        yield len(obj) + i
        i += 1

def repeat[T](value: T, count: int32) -> Iterator[T]:
    i: int32 = 0
    while i < count:
        yield value
        i += 1

def enumerate[T](iterable: Iterable[T]) -> Iterator[tuple[int32, T]]:
    i: int32 = 0
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

    # enumerate over list[int32]
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
    # the same slot fed an RVALUE: the temporary the factory call
    # materializes dies with that statement, so the frame must OWN its copy.
    # Only the committed .hpp catches a regression here -- a frame member
    # that went back to a view would print the same numbers or crash by luck,
    # never a stable output.txt diff.
    for t in len_each("rv", 3):  # tpyc: ok
        print("rvalue", t)

main()
