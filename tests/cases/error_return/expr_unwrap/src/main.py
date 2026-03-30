# Expression-level error_return unwrap.
# Tests that @error_return calls work as sub-expressions (arguments,
# binary operators) without needing explicit temp variables.
# Also tests move semantics for non-trivial types (str) and
# method chaining on unwrapped results.
from tpy import error_return, ReturnException, Own
from typing import Self
from dataclasses import dataclass

class E(Exception, ReturnException):
    pass

class ParseErr(Exception, ReturnException):
    code: int
    def __init__(self, code: int) -> None:
        self.code = code

@error_return(E)
def parse(s: str) -> int:
    if s == "":
        raise E
    return int(s)

@error_return(E)
def get_name(s: str) -> str:
    if s == "":
        raise E
    return s

@error_return(E)
def add(a: str, b: str) -> int:
    return parse(a) + parse(b)

@error_return(E)
def mul3(a: str, b: str, c: str) -> int:
    return parse(a) + parse(b) * parse(c)

@error_return(E)
def as_arg(s: str) -> int:
    return abs(parse(s))

@error_return(E)
def greet(s: str) -> str:
    return get_name(s) + " world"

@dataclass
class Point:
    x: int
    y: int

    def updated(self) -> Self:
        self.x += 1
        self.y += 1
        return self

@error_return(E)
def positive(x: int, y: int) -> Own[Point]:
    if x < 0 or y < 0:
        raise E
    return Point(x, y)

@error_return(E)
def modify(x: int, y: int) -> Own[Point]:
    return positive(x, y).updated()

def main() -> None:
    # Binary operator sub-expressions
    try:
        v = add("10", "20")
    except E:
        print("error")
    else:
        print(v)

    # Error propagation from sub-expression
    try:
        v2 = add("10", "")
    except E:
        print("caught")

    # Multiple sub-expressions
    try:
        v3 = mul3("2", "3", "4")
    except E:
        print("error")
    else:
        print(v3)

    # As function argument
    try:
        v4 = as_arg("-5")
    except E:
        print("error")
    else:
        print(v4)

    # Non-trivial type (str) -- tests move semantics
    try:
        v5 = greet("hello")
    except E:
        print("error")
    else:
        print(v5)

    try:
        v6 = greet("")
    except E:
        print("caught str")

    # Method chaining on unwrapped result
    try:
        v7 = modify(1, 2)
    except E:
        print("error")
    else:
        print(v7.x)
        print(v7.y)

    try:
        v8 = modify(-1, 2)
    except E:
        print("caught neg")

    # Direct expression-level unwrap inside try/except
    try:
        print(parse("99"))
    except E:
        print("error")

    try:
        print(add("3", ""))
    except E:
        print("caught direct")

    # except ... as e with expression-level unwrap
    test_as_binding()

@error_return(ParseErr)
def checked_parse(s: str) -> int:
    if s == "":
        raise ParseErr(1)
    if s == "?":
        raise ParseErr(2)
    return int(s)

@error_return(ParseErr)
def checked_add(a: str, b: str) -> int:
    return checked_parse(a) + checked_parse(b)

def test_as_binding() -> None:
    try:
        v = checked_add("10", "20")
    except ParseErr as e:
        print(e.code)
    else:
        print(v)

    try:
        v2 = checked_add("10", "?")
    except ParseErr as e:
        print(e.code)

    try:
        v3 = checked_add("", "5")
    except ParseErr as e:
        print(e.code)

main()
