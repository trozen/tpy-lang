# A guarded switch-tier match arm whose body falls off its end must not also
# run `case _`; only a failed guard reaches the default arm.
import asyncio
from enum import Enum, auto
from typing import Iterator
from tpy import int32


class Color(Enum):
    Red = auto()
    Green = auto()


def func(a: int32, c: bool) -> None:
    match a:
        case 3 if c:  # tpyc: ok
            print("func: guard")
        case _:
            print("func: default")


# two guarded arms on one literal: the default is the chain's last else
def two_guards(a: int32, k: int32) -> None:
    match a:
        case 3 if k == 1:
            print("two_guards: first")
        case 3 if k == 2:  # tpyc: ok
            print("two_guards: second")
        case _:
            print("two_guards: default")


# a guard body that falls off beside an unguarded same-literal arm that
# returns: the merged group still ends in `break;`, so the guard skips `case _`
def mixed(a: int32, c: bool) -> None:
    match a:
        case 3 if c:  # tpyc: ok
            print("mixed: guard")
        case 3:
            print("mixed: plain")
            return
        # an arm whose every path returns: its group drops the `break;`
        case 5:  # tpyc: ok
            print("mixed: five")
            return
        case _:
            print("mixed: default")
    print("mixed: after")


def middle(a: int32, c: bool) -> None:
    match a:
        case 1:
            print("middle: one")
        case 3 if c:  # tpyc: ok
            print("middle: guard")
        case 5:
            print("middle: five")
        case _:
            print("middle: default")


def or_arm(a: int32, c: bool) -> None:
    match a:
        case 3 | 6 if c:  # tpyc: ok
            print("or: guard")
        case _:
            print("or: default")


def enum(col: Color, c: bool) -> None:
    match col:
        case Color.Green if c:  # tpyc: ok
            print("enum: guard")
        case _:
            print("enum: default")


# the Optional partition's inner switch over the value side
def optional(a: int32 | None, c: bool) -> None:
    match a:
        case None:
            print("optional: none")
        case 3 if c:  # tpyc: ok
            print("optional: guard")
        case _:
            print("optional: default")


def no_default(a: int32, c: bool) -> None:
    # the warning is right: with no catch-all a failed guard matches nothing
    match a:  # tpyc: warning(/non-exhaustive match on 'int32'/)
        case 3 if c:  # tpyc: ok
            print("no_default: guard")
        case 4:
            print("no_default: four")
    print("no_default: after")


class K:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def method(self, c: bool) -> None:
        match self.n:
            case 3 if c:  # tpyc: ok
                print("method: guard")
            case _:
                print("method: default")


# one guarded arm that does not suspend beside one that yields
def generator(a: int32, c: bool) -> Iterator[int32]:
    match a:
        case 3 if c:  # tpyc: ok
            print("generator: guard")
        case 4 if c:
            print("generator: guard yields")
            yield 40
        case _:
            print("generator: default")
    yield 1


async def co(a: int32, c: bool) -> int32:
    match a:
        case 3 if c:  # tpyc: ok
            print("async: guard")
        case _:
            print("async: default")
    await asyncio.sleep(0)
    return 1


def nested(a: int32, b: int32, c: bool) -> None:
    match a:
        case 1:
            match b:
                case 3 if c:  # tpyc: ok
                    print("nested: inner guard")
                case _:
                    print("nested: inner default")
            print("nested: after inner")
        case _:
            print("nested: outer default")


# break and continue out of a guarded arm sit beside the else-goto
def loop(a: int32, c: bool) -> None:
    i = 0
    while i < 3:
        i += 1
        match a:
            case 3 if c:  # tpyc: ok
                print("loop: guard", i)
                if i == 2:
                    break
                continue
            case _:
                print("loop: default", i)
        print("loop: after", i)
    print("loop: done", i)


async def amain() -> None:
    for c in [True, False]:
        await co(3, c)


def main() -> None:
    for c in [True, False]:
        func(3, c)
        mixed(3, c)
        mixed(5, c)
        middle(3, c)
        or_arm(6, c)
        enum(Color.Green, c)
        optional(3, c)
        no_default(3, c)
        K(3).method(c)
        for a in [3, 4]:
            for v in generator(a, c):
                print("generator: got", v)
        nested(1, 3, c)
        loop(3, c)
    for k in [1, 2, 0]:
        two_guards(3, k)
    asyncio.run(amain())


main()
