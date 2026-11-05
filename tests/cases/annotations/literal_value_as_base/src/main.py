# A Literal value acts as its base type (a `Literal[str]` is a C++ `str`);
# a Literal slot admits only its set. Each section beside its base twin.
import asyncio
from typing import Iterator, Literal, overload
from tpy import String, dispatch, int8, int32


# print dispatch: a bool Literal prints True / False, as a bool does
def show(flag: Literal[True, False], plain: bool) -> None:
    print("print", flag, plain)  # tpyc: ok
    local: Literal[True, False] = False
    print("print local", local)


# f-string renderability over an int and a bool Literal
def fmt(level: Literal[1, 9], b: Literal[True, False], n: int, p: bool) -> None:
    print(f"fstring {level} {b} {n} {p}")  # tpyc: ok


# str() of a negative int Literal reads the parameter's own width
def conv(level: Literal[-1, 0, 9], n: int) -> None:
    print("str", str(level), str(n))  # tpyc: ok


# a str Literal parameter of a generator and a coroutine borrows as str does
def gen_lit(mode: Literal["r", "rb"]) -> Iterator[str]:  # tpyc: ok
    yield mode + "!"


def gen_str(mode: str) -> Iterator[str]:
    yield mode + "!"


async def co_lit(mode: Literal["r", "rb"]) -> str:  # tpyc: ok
    return mode + "?"


async def co_str(mode: str) -> str:
    return mode + "?"


async def run_co() -> None:
    print("coroutine", await co_lit("rb"), await co_str("rb"))


class Holder:
    mode: Literal["r", "rb"]

    def __init__(self) -> None:
        self.mode = "rb"

    # a getter copies its str field out, as a `-> str` getter does
    def get(self) -> Literal["r", "rb"]:
        return self.mode  # tpyc: warning(/returns a copy of str field 'self.mode'/)


# a str Literal returned from a parameter and a field owns its string
def ret_param(m: Literal["r", "rb"]) -> Literal["r", "rb"]:  # tpyc: ok
    return m


def ret_field(h: Holder) -> Literal["r", "rb"]:  # tpyc: ok
    return h.mode


# a long str Literal returns past the small-string buffer
def long_mode(b: bool) -> Literal["a-mode-name-longer-than-22", "x"]:
    if b:
        return "a-mode-name-longer-than-22"
    return "x"


# `case True` covers a `Literal[True]` subject
def match_true(x: Literal[True]) -> int:  # tpyc: ok
    match x:
        case True:
            return 1


# an if / elif chain over every value of a Literal needs no else
def exhaustive(m: Literal["r", "w"]) -> str:  # tpyc: ok
    if m == "r":
        return "read"
    elif m == "w":
        return "write"


# a str Literal at a `String` parameter converts as a str does
def own(s: String) -> int:
    return len(s)


def to_string(m: Literal["a", "bc"]) -> int:
    return own(m)  # tpyc: ok


# an int Literal every value of which an int8 holds needs no range check
def narrow(n: int8) -> int8:
    return n


def to_int8(x: Literal[1, 2]) -> int8:
    return narrow(x)  # tpyc: ok


# a Literal parameter flows into a larger Literal and an Optional Literal
def wider(x: Literal[1, 2, 3], m: Literal["x", "y"]) -> str:
    return f"{x}{m}"


def narrower(x: Literal[1, 2], m: Literal["x"]) -> str:
    return wider(x, m)  # tpyc: ok


def maybe(m: Literal["r", "rb"] | None) -> str:  # tpyc: ok
    if m is None:
        return "none"
    return m


# a generic binds the Literal; its value is the base's
def ident[T](x: T) -> T:
    return x


def generic(m: Literal["r", "rb"], b: Literal[True, False]) -> None:
    print("generic", ident(m), ident(b))  # tpyc: ok


# a local only ever bound to values of the set passes into the Literal slot
def take_mode(m: Literal["r", "w"]) -> str:
    return "mode " + m


def take_level(n: Literal[1, 2]) -> int:
    return n * 10


def take_flag(f: Literal[True]) -> bool:
    return f


def pending_locals(c: bool) -> None:
    s = "r"
    if c:
        s = "w"
    n = 1
    f = True
    print("pending", take_mode(s), take_level(n), take_flag(f))  # tpyc: ok


# out of the Literal stub's set, a local takes the str stub
@overload
def pick(m: Literal["r"]) -> str: ...
@overload
def pick(m: str) -> str: ...
def pick(m: str) -> str:
    return m + "!"


def fallback() -> None:
    s = "x"
    t = "r"
    print("fallback", pick(s), pick(t))  # tpyc: ok


# a call that takes no address of a local leaves it literal-only
def echo(s: str) -> str:
    return s


@overload
def litr(x: Literal["r"]) -> int32: ...
@overload
def litr(x: str) -> int: ...
def litr(x: str) -> int:
    return len(x)


def no_address() -> None:
    s = "r"
    echo(s)
    n = litr(s)  # tpyc: type(int32)
    print("no address", n)


# a local some binding of which is no literal store takes the base stub
@overload
def lvl(n: Literal[1]) -> str: ...
@overload
def lvl(n: int) -> str: ...
def lvl(n: int) -> str:
    return f"lvl{n}"


def disqualified(p: str) -> None:
    n = 0
    n += 1
    s = p
    t = "x"

    def rebind() -> None:
        nonlocal t
        t = "r"
    rebind()
    print("disqualified", lvl(n), pick(s), pick(t))  # tpyc: ok


# isinstance tests a Literal member of a union as its base type
def isin_str(mode: Literal["r", "rb"] | int) -> int:
    if isinstance(mode, str):  # tpyc: ok
        return len(mode)
    return mode


def isin_bool(x: Literal[True] | int) -> str:
    if isinstance(x, bool):  # tpyc: ok
        return "bool"
    return "other"


def isin_case(mode: Literal["r", "rb"] | int) -> int:
    match mode:
        case str():  # tpyc: ok
            return len(mode)
        case int():
            return mode
    return 0


# the int member matched, the Literal member taken in the else branch
def isin_else(mode: Literal["r", "rb"] | int) -> int:
    if isinstance(mode, int):
        return mode
    else:
        return len(mode)  # tpyc: ok


# the int member returned early, the Literal member extracted after the if
def isin_return(mode: Literal["r", "rb"] | int) -> int:
    if isinstance(mode, int):
        return mode
    return len(mode)  # tpyc: ok


# a negated isinstance returns the Literal member early, the int after
def isin_not(mode: Literal["r", "rb"] | int) -> int:
    if not isinstance(mode, int):
        return len(mode)  # tpyc: ok
    return mode


# an equality test refines a Literal subject's set and extracts nothing
def refine(mode: Literal["r", "rb"]) -> int:
    if mode == "r":
        return 10
    return len(mode)  # tpyc: ok


# an int literal outside a Literal variant's set reaches a float variant
@dispatch
def lit_or_float(x: Literal[1, 2]) -> str:
    return "literal"


@dispatch
def lit_or_float(x: float) -> str:
    return "float"


# a BigInt-based Literal copy into an int32 Literal local converts its base
@overload
def store(x: Literal[2]) -> int: ...
@overload
def store(x: Literal[3]) -> int: ...
def store(x: int) -> int:
    y: Literal[2, 3] = x  # tpyc: ok
    return y * 10


# a call result binds owned, in a function, a loop, a generator and async
def gen_bind(b: bool) -> Iterator[str]:
    m = long_mode(b)  # tpyc: ok
    yield m + "<"


async def co_bind() -> str:
    m = long_mode(True)
    await asyncio.sleep(0)
    return m


async def run_bind() -> None:
    print("bind async", await co_bind())


def bind_results() -> None:
    m = ret_param("r")
    loop = ""
    for b in [True, False]:
        loop += long_mode(b) + ";"
    print("bind", m, loop, [x for x in gen_bind(True)])
    asyncio.run(run_bind())


def main() -> None:
    show(True, True)
    show(False, False)
    fmt(9, True, 9, True)
    conv(-1, -1)
    print("generator", [m for m in gen_lit("r")], [m for m in gen_str("r")])
    asyncio.run(run_co())
    h = Holder()
    a = ret_param("rb")
    b = ret_field(h)
    g = h.get()
    h.mode = "r"  # the returned strings are their own
    print("return", a, b, g, h.mode, a == "rb", b == "rb")
    print("long", long_mode(True), long_mode(False))
    print("match", match_true(True), exhaustive("r"), exhaustive("w"))
    print("string", to_string("bc"), to_int8(2))
    print("wider", narrower(2, "x"), maybe("rb"), maybe(None))
    generic("rb", False)
    xb: list[Literal[True, False]] = [True, False]
    print("element", str(xb[1]), xb[0])  # tpyc: ok
    pending_locals(True)
    fallback()
    no_address()
    disqualified("r")
    print("isinstance", isin_str(3), isin_str("rb"), isin_bool(True),
          isin_bool(3), isin_case(3), isin_case("rb"), isin_else(3),
          isin_else("r"))
    print("isinstance return", isin_return(3), isin_return("rb"),
          refine("r"), refine("rb"))
    print("isinstance not", isin_not(3), isin_not("rb"))
    print("store", store(2), store(3))
    print("dispatch", lit_or_float(1), lit_or_float(5))  # tpyc: ok
    bind_results()


main()
