# Value steps and non-step `Own` returns fit lending slots: around a value
# type `Own` hands nothing observable over.
from typing import Iterable, Iterator, Protocol, Self
from tpy import Own, ValueType, dynamic, int32
from fixtures import Cell, sum_lent


@dynamic
class PlainText(Protocol):
    def f(self) -> str: ...


@dynamic
class OwnText(Protocol):
    def f(self) -> Own[str]: ...


class PlainFirst(PlainText, OwnText, Protocol):
    pass


class OwnFirst(OwnText, PlainText, Protocol):
    pass


class TextR(PlainFirst):
    def __init__(self) -> None:
        pass

    def f(self) -> str:
        return "r"


class TextQ(OwnFirst):
    def __init__(self) -> None:
        pass

    def f(self) -> str:
        return "q"


def text_of[T: OwnFirst](x: T) -> str:
    return x.f()


class IntCounter:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> Own[int32]:
        if self.n >= 3:
            raise StopIteration
        self.n += 1
        return self.n


class Runner[R](Protocol):
    def run(self) -> R: ...


class Task:
    def run(self) -> Own[list[str]]:
        return ["done"]


def run_it(r: Runner[list[str]]) -> int32:
    return len(r.run())


class CountIter:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> int32:
        if self.n >= 2:
            raise StopIteration
        self.n += 1
        return self.n


class Holder:
    def __iter__(self) -> Own[CountIter]:
        return CountIter()


G: list[Cell] = [Cell(40), Cell(41)]


class ValueSteps[T: ValueType](Iterator[T], Protocol):
    # a value-bound step: `Own` around it hands nothing observable over
    def __next__(self) -> Own[T]: ...


def sum_value_steps(xs: ValueSteps[int32]) -> int32:
    return sum_lent(xs)


class Mixed[T](Protocol):
    def token(self) -> T: ...
    def __iter__(self) -> Iterator[Cell]: ...


class Lender:
    def token(self) -> int32:
        return 7

    def __iter__(self) -> Iterator[Cell]:
        for c in G:
            yield c


def first(xs: Iterable[Cell]) -> Cell:
    for c in xs:
        return c
    raise ValueError("empty")


def consume(xs: Mixed[Own[int32]]) -> int32:
    return xs.token() + first(xs).v


def main() -> None:
    # a nominal filler's `-> str` meets a combined `Own[str]` / `str` slot
    # in either parent order: around a value type `Own` changes nothing
    print("value-type own", text_of(TextR()), text_of(TextQ()))  # tpyc: ok
    # a value step fits a lending slot whatever its `Own`
    print("value step", sum_lent(IntCounter()))  # tpyc: ok
    # a non-step `Own` return: a task result, an iterator object
    print("own result", run_it(Task()))  # tpyc: ok
    print("own iterator object", sum_lent(Holder()))  # tpyc: ok
    # an `Own` type argument on a non-step member leaves the step lending
    print("mixed", consume(Lender()))  # tpyc: ok
    # a value-bound sub-protocol redeclaring `__next__ -> Own[T]` still
    # lends at a lending slot
    print("value-bound child", sum_value_steps(IntCounter()))  # tpyc: ok
