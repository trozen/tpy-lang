# A return-only exception is a plain value, never a thrown object: what stays
# observable on one after `except X as e` (message, str, fields, re-raise),
# per position. `except X as e` binds the error itself, not a copy: the
# "mutate" section changes it through `e` before a bare `raise` and reads the
# change in the outer handler.
from typing import Iterator
from tpy import int32, error_return, ReturnException
from errors import Denied, check


class Empty(Exception, ReturnException):
    pass


# A return exception carries only what it declares: `message` is an ordinary
# field here, not the one a thrown Exception inherits.
class Missing(Exception, ReturnException):
    message: str  # tpyc: ok

    def __init__(self, message: str = "") -> None:
        self.message = message


class ParseError(Exception, ReturnException):
    line: int32
    detail: str

    def __init__(self, line: int32, detail: str) -> None:
        self.line = line
        self.detail = detail


@error_return(Missing)
def find(key: str) -> int32:
    if key == "a":
        return 1
    raise Missing("no " + key)


# A destructor deletes the copy constructor and gives the class a user-declared
# move constructor, whose base initialiser must name the value base too. The
# error is MOVED at every step -- raise, propagation, the handler's slot -- so
# a class that cannot be copied still travels.
class Tracked(Exception, ReturnException):
    code: int32

    def __init__(self, code: int32) -> None:
        self.code = code

    def __del__(self) -> None:
        pass


# A class may render itself: its own __str__ wins over the declared message.
class Labelled(Exception, ReturnException):
    message: str

    def __init__(self, message: str) -> None:
        self.message = message

    def __str__(self) -> str:
        return "labelled:" + self.message


@error_return(Tracked)
def track(code: int32) -> int32:
    if code == 0:
        return 0
    raise Tracked(code)  # tpyc: ok


# propagation: the non-copyable error passes through a second frame, from a
# statement (`first = ...`) and from inside an expression (`... + 1`)
@error_return(Tracked)
def track_twice(code: int32) -> int32:
    first = track(code)  # tpyc: ok
    return first + track(code) + 1  # tpyc: ok


@error_return(Labelled)
def label(ok: bool) -> int32:
    if ok:
        return 1
    raise Labelled("x")


# mutate: the handler changes the bound error, then hands the same value on
@error_return(ParseError)
def parse_relabelled(s: str) -> int32:
    try:
        v = parse(s)
    except ParseError as e:
        e.line = 99  # tpyc: ok
        raise
    return v


@error_return(Empty)
def probe(ok: bool) -> int32:
    if ok:
        return 5
    raise Empty


@error_return(ParseError)
def parse(s: str) -> int32:
    if s == "ok":
        return 7
    raise ParseError(3, "bad " + s)


# propagation: an unhandled error passes through with its fields intact
@error_return(ParseError)
def parse_twice(s: str) -> int32:
    return parse(s) + parse(s)


# re-raise: a bare `raise` in the handler hands the same value on
@error_return(ParseError)
def parse_logged(s: str) -> int32:
    try:
        v = parse(s)
    except ParseError as e:
        print("reraise: saw line", e.line)
        raise
    return v


@error_return(StopIteration)
def first_even(xs: list[int32]) -> int32:
    for x in xs:
        if x % 2 == 0:
            return x
    raise StopIteration


class Reader:
    seen: int32

    def __init__(self) -> None:
        self.seen = 0

    # method: the handler binds the error inside a method body
    def read(self, s: str) -> int32:
        try:
            v = parse(s)
        except ParseError as e:
            self.seen += 1
            return -e.line
        return v


def count(n: int32) -> Iterator[int32]:
    for i in range(n):
        yield i + 1


def pairs(it: Iterator[int32]) -> Iterator[int32]:
    while True:
        # generator: exhaustion of the inner generator is handled in the frame
        try:
            a = next(it)
        except StopIteration:
            return
        yield a * 10


def main() -> None:
    # free function, builtin: the bound StopIteration carries an empty message
    try:
        v_builtin = first_even([1, 3])
        print("builtin:", v_builtin)
    except StopIteration as e:
        print("builtin: str empty", str(e) == "")

    # free function, empty: a `pass` class carries nothing, so str() is empty
    try:
        v_empty = probe(False)
        print("empty:", v_empty)
    except Empty as e:
        print("empty: str empty", str(e) == "")

    # free function, message: str() reads the declared `message` field
    try:
        v_message = find("zz")
        print("message:", v_message)
    except Missing as e:
        print("message:", str(e))

    # free function, fields
    try:
        v_fields = parse("x")
        print("fields:", v_fields)
    except ParseError as e:
        print("fields:", e.line, e.detail)

    try:
        v_propagate = parse_twice("y")
        print("propagate:", v_propagate)
    except ParseError as e:
        print("propagate:", e.line, e.detail)

    try:
        v_reraise = parse_logged("z")
        print("reraise:", v_reraise)
    except ParseError as e:
        print("reraise:", e.detail)

    # free function, destructor: the error is moved into and out of the result
    try:
        v_dtor = track(9)
        print("dtor:", v_dtor)
    except Tracked as e:
        print("dtor:", e.code)

    try:
        v_dtor2 = track_twice(4)
        print("dtor propagated:", v_dtor2)
    except Tracked as e:
        print("dtor propagated:", e.code)

    # free function, own __str__ beside a declared message
    try:
        v_label = label(False)
        print("own str:", v_label)
    except Labelled as e:
        print("own str:", str(e))

    try:
        v_mut = parse_relabelled("m")
        print("mutate:", v_mut)
    except ParseError as e:
        print("mutate:", e.line, e.detail)

    # cross-module: str() of a class declared in another module
    try:
        v_cross = check(1)
        print("cross:", v_cross)
    except Denied as e:
        print("cross:", str(e))

    r = Reader()
    print("method:", r.read("ok"), r.read("q"), r.seen)

    for v in pairs(count(2)):
        print("generator:", v)


main()
