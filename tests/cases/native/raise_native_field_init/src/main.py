# `raise X(args)` of a @native class with no __init__ builds it through its field
# constructor, as the expression `X(args)` does, at both tiers.
__tpy__ = {"includes": ["native_types.hpp"]}
from tpy import ReturnException, error_return, int32
from tpy.extern import native


@native("NativeErr")
class NativeErr(Exception, ReturnException):
    code: int32


@native("NativeThrow")
class NativeThrow(Exception):
    code: int32
    extra: int32


def throw_tier(k: int32) -> None:
    # the subject: a throw-tier binding's field constructor, keyword and positional
    raise NativeThrow(k, extra=k * 2)  # tpyc: ok


@error_return(NativeErr)
def positional(k: int32) -> int32:
    if k > 0:
        # the subject: the field constructor takes the positional argument
        raise NativeErr(k)  # tpyc: ok
    return k


@error_return(NativeErr)
def keyword(k: int32) -> int32:
    if k > 0:
        # the subject: and the keyword one
        raise NativeErr(code=k + 1)  # tpyc: ok
    return k


def main() -> None:
    try:
        positional(1)
    except NativeErr as e:
        print("positional", e.code)
    try:
        keyword(1)
    except NativeErr as e:
        print("keyword", e.code)
    try:
        throw_tier(3)
    except NativeThrow as t:
        print("throw", t.code, t.extra)


main()
