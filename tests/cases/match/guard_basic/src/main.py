# match/case with guard clauses (if conditions); the terminator sections pin
# that an arm body which never falls out gets no goto/break after it -- each
# is followed by an arm on the same subject that prints, so a fall-through
# would show up in the output.
from dataclasses import dataclass
from enum import Enum

@dataclass
class Dog:
    name: str

@dataclass
class Cat:
    name: str

class Color(Enum):
    RED = 1
    BLUE = 2

def describe(a: Dog | Cat) -> str:
    match a:
        case Dog(name=n) if n == "Rex":
            return "Rex the dog!"
        case Dog():
            return "some dog"
        case Cat(name=n) if n == "Whiskers":
            return "Whiskers the cat!"
        case _:
            return "other"
    return ""

# Guarded if-chain tier (an `int` subject), arm ends in `raise`.
def chain_raise(n: int, flag: bool) -> str:
    match n:
        case 1 if flag:
            raise ValueError("chain_raise")  # tpyc: ok
        case 1:
            print("chain_raise: next arm")
            return "fell"
        case _:
            return "other"

# Guarded if-chain tier, arm ends in an if/else that returns on both paths.
def chain_if_else(n: int, flag: bool, pick: bool) -> str:
    match n:
        case 1 if flag:
            if pick:  # tpyc: ok
                return "then"
            else:
                return "else"
        case 1:
            print("chain_if_else: next arm")
            return "fell"
        case _:
            return "other"

# Guarded if-chain tier, arm ends in try/finally whose body returns.
def chain_try_finally(n: int, flag: bool) -> str:
    match n:
        case 1 if flag:
            try:  # tpyc: ok
                return "ret"
            finally:
                print("chain_try_finally: finally")
        case 1:
            print("chain_try_finally: next arm")
            return "fell"
        case _:
            return "other"

# Guarded if-chain tier, `break` at the end of an arm inside a for loop.
def chain_break(xs: list[int], flag: bool) -> None:
    for x in xs:
        match x:
            case 2 if flag:
                print("chain_break: break at", x)
                break  # tpyc: ok
            case _:
                print("chain_break: saw", x)

# Guarded union switch tier, arm ends in a nested exhaustive match whose
# arms all return.
def union_nested_match(a: Dog | Cat, c: Color) -> str:
    match a:
        case Dog(name=n) if n == "Rex":
            match c:  # tpyc: ok
                case Color.RED:
                    return "rex-red"
                case Color.BLUE:
                    return "rex-blue"
        case Dog():
            print("union_nested_match: next arm")
            return "fell"
        case _:
            return "other"

# Guarded union switch tier, arm ends in `while True:` with no break.
def union_while_true(a: Dog | Cat) -> int:
    match a:
        case Dog(name=n) if n == "Rex":
            i = 0
            while True:  # tpyc: ok
                i += 1
                if i == 3:
                    return i
        case Dog():
            print("union_while_true: next arm")
            return -1
        case _:
            return 0

# Guarded union switch tier, arm ends in `assert False`.
def union_assert_false(a: Dog | Cat) -> str:
    match a:
        case Dog(name=n) if n == "Rex":
            assert False  # tpyc: ok
        case Dog():
            print("union_assert_false: next arm")
            return "fell"
        case _:
            return "other"

# Guarded union switch tier, `break` at the end of an arm inside a for loop.
def union_break(pets: list[Dog | Cat]) -> None:
    for p in pets:
        match p:
            case Cat(name=n) if n == "Luna":
                print("union_break: break at", n)
                break  # tpyc: ok
            case Cat():
                print("union_break: next arm")
            case _:
                print("union_break: dog")

# Unguarded union switch tier, arm ends in `while True:` with no break: the
# case block ends without a `break;` and must not trip -Wimplicit-fallthrough.
def switch_while_true(a: Dog | Cat) -> int:
    match a:
        case Dog():
            i = 0
            while True:  # tpyc: ok
                i += 1
                if i == 2:
                    return i
        case Cat():
            print("switch_while_true: cat")
    return 0

# Unguarded union switch tier, arm ends in try/finally whose body returns.
def switch_try_finally(a: Dog | Cat) -> str:
    match a:
        case Dog():
            try:  # tpyc: ok
                return "ret"
            finally:
                print("switch_try_finally: finally")
        case Cat():
            print("switch_try_finally: cat")
    return "after"

def main() -> None:
    d1: Dog | Cat = Dog("Rex")
    d2: Dog | Cat = Dog("Buddy")
    c1: Dog | Cat = Cat("Whiskers")
    c2: Dog | Cat = Cat("Luna")
    print(describe(d1))
    print(describe(d2))
    print(describe(c1))
    print(describe(c2))
    try:
        chain_raise(1, True)
    except ValueError:
        print("chain_raise: caught")
    r = chain_raise(1, False)
    print("chain_raise:", r)
    r = chain_if_else(1, True, False)
    print("chain_if_else:", r)
    r = chain_if_else(1, False, False)
    print("chain_if_else:", r)
    r = chain_try_finally(1, True)
    print("chain_try_finally:", r)
    chain_break([1, 2, 3], True)
    r = union_nested_match(d1, Color.BLUE)
    print("union_nested_match:", r)
    r = union_nested_match(d2, Color.RED)
    print("union_nested_match:", r)
    k = union_while_true(d1)
    print("union_while_true:", k)
    try:
        union_assert_false(d1)
    except AssertionError:
        print("union_assert_false: caught")
    r = union_assert_false(d2)
    print("union_assert_false:", r)
    union_break([Dog("Buddy"), Cat("Whiskers"), Cat("Luna"), Dog("Rex")])
    k = switch_while_true(d1)
    print("switch_while_true:", k)
    k = switch_while_true(c1)
    print("switch_while_true:", k)
    r = switch_try_finally(d1)
    print("switch_try_finally:", r)
    r = switch_try_finally(c1)
    print("switch_try_finally:", r)

main()
