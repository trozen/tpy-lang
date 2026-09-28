# A name bound in sibling arms (match arms, try body and handlers, if arms) is one
# local typed by the join of every binding; pattern captures keep per-arm storage.
import asyncio
from typing import Iterator
from tpy import int32, int64, error_return, ReturnException


class ParseError(Exception, ReturnException):
    pass


@error_return(ParseError)
def parse_digit(s: str) -> int32:
    if s == "0":
        return 0
    raise ParseError


class Cat:
    def __init__(self, lives: int32) -> None:
        self.lives = lives


class Dog:
    def __init__(self, nick: str) -> None:
        self.nick = nick


# match arms: fresh name bound to an int64 and to an int literal
def match_arms(k: int32, big: int64) -> None:
    match k:
        case 1:
            y = big
        case _:
            y = 3  # tpyc: type(int64)
    print("match_arms", y)


# try body + two handlers (C++ try/catch tier)
def try_handlers(s: str, big: int64) -> None:
    try:
        n = 3
        int(s)
    except ValueError:
        n = big  # tpyc: type(int64)
    except KeyError:
        n = 0
    print("try_handlers", n)


# try body + handler over an @error_return call (return tier)
def try_error_return(s: str, big: int64) -> None:
    try:
        d = big
        parse_digit(s)
    except ParseError:
        d = 3  # tpyc: type(int64)
    print("try_error_return", d)


# None-seeded local bound in both if arms joins to one Optional slot
def none_if(c: bool) -> None:
    x = None
    if c:
        x = 3
    else:
        x = 4  # tpyc: ok
    if x is not None:
        print("none_if", x + 1)


# a local bound before the if, widened by the then-arm only
def widen_existing(c: bool, big: int64) -> None:
    y = 3
    if c:
        y = big
    else:
        y = 4  # tpyc: type(int64)
    print("widen_existing", y)


# pattern captures bound at different types per arm keep per-arm storage
def capture_types(a: Cat | Dog) -> None:
    match a:
        case Cat(lives=v):
            print("capture_types", v + 1)
        case Dog(nick=v):  # tpyc: ok
            print("capture_types", v + "!")


# a pattern capture in one arm and a statement binding in the sibling arm
def capture_or_stmt(a: Cat | Dog) -> None:
    match a:
        case Cat(lives=v):
            print("capture_or_stmt cat", v)
        case _:
            v = 0  # tpyc: ok
    print("capture_or_stmt", v)


# match arms inside a generator
def gen_arms(k: int32, big: int64) -> Iterator[int64]:
    yield 0
    match k:
        case 1:
            y = big
        case _:
            y = 3  # tpyc: type(int64)
    yield y


# match arms inside an async function, read across a suspension
async def async_arms(k: int32, big: int64) -> int64:
    await asyncio.sleep(0)
    match k:
        case 1:
            y = big
        case _:
            y = 3  # tpyc: type(int64)
    await asyncio.sleep(0)
    return y


# annotated Optional first bound in one match arm, None in the other
def match_hoist_opt(k: int32, n: int32) -> None:
    match k:
        case 1:
            v: int32 | None = n
        case _:
            v = None
    if v is None:  # tpyc: ok
        print("match_hoist_opt none")
    else:
        # the narrowed read of the hoisted Optional unwraps it
        print("match_hoist_opt", v + 1)  # tpyc: ok


# unannotated None first in one match arm, an int32 in the other
def match_hoist_none_first(k: int32, n: int32) -> None:
    match k:
        case 1:
            v = None
        case _:
            v = n
    if v is not None:
        print("match_hoist_none_first", v + 1)  # tpyc: ok
    else:
        print("match_hoist_none_first none")


# annotated Optional first bound in a try body, None in the handler
def try_hoist_opt(s: str, n: int32) -> None:
    try:
        v: int32 | None = n
        int(s)
    except ValueError:
        v = None
    if v is None:  # tpyc: ok
        print("try_hoist_opt none")
    else:
        print("try_hoist_opt", v)


# match arms binding an int expression and an int literal join at int (BigInt)
def match_bigint(c: int) -> int:
    match c:
        case 1:
            n = c + 1
        case _:
            n = 0  # tpyc: type(int)
    return n


# match arm whose binding sits in a nested if read after it; every arm returns
def match_nested(k: int32) -> None:
    match k:
        case 2:
            r = "two"
            print("match_nested", r)
            return
        case _:
            if k == 3:
                r = "three"
            else:
                r = "other"
            print("match_nested", r)  # tpyc: ok
            return


# else arm whose binding sits in a nested if read after it; both arms return
def if_nested(k: int32) -> None:
    if k == 2:
        r = "two"
        print("if_nested", r)
        return
    else:
        if k == 3:
            r = "three"
        else:
            r = "other"
        print("if_nested", r)  # tpyc: ok
        return


# a later handler whose binding sits in a nested if read after it
def try_nested(s: str, k: int32) -> None:
    table = {"a": 1}
    try:
        n = int(s)
        n += table[s]
        print("try_nested", n)
    except KeyError:
        r = "key"
        print("try_nested", r)
    except ValueError:
        if k == 3:
            r = "three"
        else:
            r = "other"
        print("try_nested", r)  # tpyc: ok


def main() -> None:
    match_arms(1, 10000000000)
    match_arms(2, 10000000000)
    try_handlers("4", 10000000000)
    try_handlers("x", 10000000000)
    try_error_return("0", 10000000000)
    try_error_return("x", 10000000000)
    none_if(True)
    none_if(False)
    widen_existing(True, 10000000000)
    widen_existing(False, 10000000000)
    capture_types(Cat(9))
    capture_types(Dog("rex"))
    capture_or_stmt(Cat(9))
    capture_or_stmt(Dog("rex"))
    for g in gen_arms(1, 10000000000):
        print("gen_arms", g)
    for g in gen_arms(2, 10000000000):
        print("gen_arms", g)
    print("async_arms", asyncio.run(async_arms(1, 10000000000)))
    print("async_arms", asyncio.run(async_arms(2, 10000000000)))
    match_hoist_opt(1, 5)
    match_hoist_opt(2, 5)
    match_hoist_none_first(1, 5)
    match_hoist_none_first(2, 5)
    try_hoist_opt("4", 5)
    try_hoist_opt("x", 5)
    print("match_bigint", match_bigint(1), match_bigint(10 ** 20))
    for k in range(2, 5):
        match_nested(k)
        if_nested(k)
        try_nested("1", k)
        try_nested("x", k)


def big_value() -> int64:
    return 10000000000


main()

# module level: a global bound to an int64 in the taken if arm and to 3 in the other
if len("ab") == 2:
    mod_if = big_value()
else:
    mod_if = 3  # tpyc: type(int64)
print("module_if", mod_if)

# module level: a global bound to an int64 in the try body and to 3 in the handler
try:
    mod_try = big_value()
    int("1")
except ValueError:
    mod_try = 3  # tpyc: type(int64)
print("module_try", mod_try)
