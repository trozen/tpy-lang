# Container / str / record truthiness of a generator-frame local. These
# forms have no C++ `operator bool` at all, so before the fix the emitted
# code did not compile ("could not convert std::vector<...> to bool") --
# the `.empty()` / `__len__` / `__bool__` dispatch the sync path applies
# was skipped for a frame-resident read.
from enum import Enum, IntEnum, auto
from typing import Any, Iterator
from tpy import Int32


class Color(Enum):
    RED = auto()
    BLUE = auto()


class Level(IntEnum):
    ZERO = 0
    HIGH = 5


class Plain:
    # Neither __bool__ nor __len__ -- Python's default object truthiness.
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class Bag:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __len__(self) -> Int32:
        return self.n


class Flag:
    on: bool

    def __init__(self, on: bool) -> None:
        self.on = on

    def __bool__(self) -> bool:
        return self.on


def drain_list(xs: list[Int32]) -> Iterator[Int32]:
    # The peephole shape, and the reproducer BUGS.md carried: the loop
    # mutates the frame-resident list, so an empty one must stop it.
    while xs:
        yield xs.pop()


def drain_dict(d: dict[Int32, Int32], order: list[Int32]) -> Iterator[Int32]:
    # `order` keeps the drain deterministic across dict implementations;
    # the point under test is the `while d:` head.
    i = 0
    while d:
        yield d.pop(order[i])
        i += 1


def drain_set(s: set[Int32]) -> Iterator[Int32]:
    while s:
        yield s.pop()


def str_branch(t: str) -> Iterator[Int32]:
    if t:
        yield 1
    yield 2


def bytes_branch(b: bytes) -> Iterator[Int32]:
    if b:
        yield 1
    yield 2


def record_len_branch(g: Bag) -> Iterator[Int32]:
    if g:
        yield 1
    yield 2


def record_bool_branch(f: Flag) -> Iterator[Int32]:
    if f:
        yield 1
    yield 2


# The remaining arms of the same truthiness dispatch. Each renders its own
# way (enum folds to always-true, IntEnum tests the underlying value, Any
# goes through to_bool), and each was a build error in this position before.
def enum_branch(c: Color) -> Iterator[Int32]:
    if c:
        yield 1
    yield 2


def int_enum_branch(lv: Level) -> Iterator[Int32]:
    if lv:
        yield 1
    yield 2


def plain_record_branch(p: Plain) -> Iterator[Int32]:
    if p:
        yield 1
    yield 2


def any_branch(v: Any) -> Iterator[Int32]:
    if v:
        yield 1
    yield 2


def and_branch(xs: list[Int32], t: str) -> Iterator[Int32]:
    # A boolop recurses into both operands, so each side takes its own
    # truthiness render rather than the whole expression taking one.
    if xs and t:
        yield 1
    yield 2


def peephole_or(xs: list[Int32], t: str) -> Iterator[Int32]:
    # The same recursion, in the simple-generator while peephole. `rest` is
    # a local because a str param cannot be rebound; clearing the borrowed
    # list plus emptying `rest` ends the loop after one pass.
    rest = t
    while xs or rest:
        yield 1
        xs.clear()
        rest = ""


def main() -> None:
    xs = [1, 2, 3]
    print("list", list(drain_list(xs)))
    # The generator drained the caller's list -- it was borrowed, not copied.
    print("drained", xs)
    print("empty", list(drain_list([])))
    # Each drain is re-observed through the CALLER's binding: the generator
    # borrowed the container, so the caller must see it emptied. A silent
    # copy at the boundary would leave these printing the original contents.
    dd = {1: 10, 2: 20}
    print("dict", list(drain_dict(dd, [2, 1])), dd)
    print("dict0", list(drain_dict({}, [])))
    ss = {7, 9}
    print("set", sorted(list(drain_set(ss))), ss)
    print("set0", list(drain_set(set())))
    print("str", list(str_branch("x")), list(str_branch("")))
    print("bytes", list(bytes_branch(b"x")), list(bytes_branch(b"")))
    print("len", list(record_len_branch(Bag(3))),
          list(record_len_branch(Bag(0))))
    print("bool", list(record_bool_branch(Flag(True))),
          list(record_bool_branch(Flag(False))))
    # Enum members are always truthy; an IntEnum tests its value, so ZERO
    # is falsy. A dunder-less record is always truthy (Python default).
    print("enum", list(enum_branch(Color.RED)), list(enum_branch(Color.BLUE)))
    print("intenum", list(int_enum_branch(Level.HIGH)),
          list(int_enum_branch(Level.ZERO)))
    print("plain", list(plain_record_branch(Plain(0))))
    print("any", list(any_branch(1)), list(any_branch(0)))
    print("and", list(and_branch([1], "x")), list(and_branch([], "x")),
          list(and_branch([1], "")))
    print("or", list(peephole_or([1], "")), list(peephole_or([], "")))


main()
