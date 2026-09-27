# A `Literal[...]`-typed parameter or field takes a default from its value set,
# at every position a default reaches; each section prints under its own name.
# Methods and constructors have no section: a call to one with a Literal param
# is not lowered yet (BUGS.md#literal-param-proxy-gates-reject-plain-call).
import asyncio
from typing import Iterator, Literal, overload


# free function, str Literal
def free_str(mode: Literal["r", "rb"] = "rb") -> str:  # tpyc: ok
    return mode


# keyword-only parameter
def kwonly(x: int, *, mode: Literal["r", "rb"] = "rb") -> str:  # tpyc: ok
    return str(x) + mode


# int Literal, negative member
def int_neg(level: Literal[-1, 0, 9] = -1) -> int:  # tpyc: ok
    return level


# bool Literal
def bool_lit(flag: Literal[True] = True) -> bool:  # tpyc: ok
    return flag


# Optional[Literal] with a non-None default
def optional(mode: Literal["r", "rb"] | None = "rb") -> str:  # tpyc: ok
    if mode is None:
        return "none"
    return mode


# a default skipped over by a keyword call is filled in at the call site
def keyword_skip(mode: Literal["r", "rb"] = "rb", n: int = 1) -> str:  # tpyc: ok
    return mode + str(n)


# equality narrowing on the defaulted parameter
def narrowing(mode: Literal["r", "rb"] = "rb") -> str:  # tpyc: ok
    if mode == "r":
        return "text"
    return "binary:" + mode


# generator (the param is captured as an owned string, not a view:
# BUGS.md#literal-value-consumers-miss-base)
def generator(mode: Literal["r", "rb"] = "rb") -> Iterator[str]:  # tpyc: ok
    yield mode


# async (same owned capture as the generator)
async def async_fn(mode: Literal["r", "rb"] = "rb") -> str:  # tpyc: ok
    return mode


# @overload stub carrying the default; it equals the implementation's, since a
# differing one runs the wrong specialization (BUGS.md#overload-stub-default-ignored)
@overload
def stub(mode: Literal["r"]) -> str: ...
@overload
def stub(mode: Literal["rb"] = "rb") -> str: ...  # tpyc: ok
def stub(mode: str = "rb") -> str:
    return "stub:" + mode


# record field default
class Opts:
    mode: Literal["r", "rb"] = "rb"  # tpyc: ok


async def run_async() -> None:
    print("async_fn", await async_fn(), await async_fn("r"))


def main() -> None:
    print("free_str", free_str(), free_str("r"))
    print("kwonly", kwonly(1), kwonly(2, mode="r"))
    print("int_neg", int_neg(), int_neg(9))
    print("bool_lit", bool_lit(), bool_lit(True))
    print("optional", optional(), optional("r"), optional(None))
    print("keyword_skip", keyword_skip(n=2), keyword_skip(), keyword_skip(mode="r"))
    print("narrowing", narrowing(), narrowing("r"))
    for m in generator():
        print("generator", m)
    for m in generator("r"):
        print("generator", m)
    asyncio.run(run_async())
    print("stub", stub(), stub("r"), stub("rb"))
    o = Opts()
    print("field", o.mode)


main()
