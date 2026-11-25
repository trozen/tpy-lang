# bool(x) of a builtin's subclass, in a generic method and in a lambda body.
from typing import Callable
from tpy import Truthy, String, StrView, int64


class SubStr(str):
    pass


class SubString(String):
    pass


class SubInt(int):
    pass


class LoudStr(str):  # tpyc: warning(/hides 'str.__bool__'/)
    # a subclass's own __bool__ overrides the inherited one
    def __bool__(self) -> bool:
        return True


class NeInt(int):  # tpyc: warning(/hides 'int.__eq__'/)
    # its own != / == are not what the inherited truth test runs
    def __eq__(self, other: int) -> bool:
        return True

    def __ne__(self, other: int) -> bool:
        return False


class EmptyStr(str):
    # a method named like the C++ test is not what the inherited test runs
    def empty(self) -> bool:
        return False


class LenStr(str):  # tpyc: warning(/hides 'str.__len__'/)
    # a str's truth is its length: the overriding __len__ decides
    def __len__(self) -> int:
        return 1


class WideLenStr(str):  # tpyc: warning(/hides 'str.__len__'/)
    # a length past int32 is still non-zero
    def __len__(self) -> int64:
        return int64(4294967296)


class ZeroLenStr(str):  # tpyc: warning(/hides 'str.__len__'/)
    # a zero length is false over non-empty text
    def __len__(self) -> int64:
        return int64(0)


class View(StrView):
    pass


def gb[T: Truthy](x: T) -> bool:
    return bool(x)


class Probe:
    def m[T: Truthy](self, x: T) -> bool:
        # a generic method body: the same variant as the direct call
        return bool(x)  # tpyc: ok


def subclasses() -> None:
    # a builtin's subclass inherits its __bool__
    s = SubStr()
    st = SubString()
    print("subclass", bool(s), bool(st), gb(s), gb(st))  # tpyc: ok
    n = SubInt(0)
    k = SubInt(3)
    print("subclass int", bool(n), gb(n), bool(k), gb(k))  # tpyc: ok
    loud = LoudStr()
    print("subclass own", bool(loud), gb(loud))  # tpyc: ok
    print("subclass full", bool(SubStr("x")), gb(SubStr("x")), bool(SubString("x")), gb(SubString("x")))
    ne = NeInt(3)
    print("subclass ne", bool(ne), gb(ne), bool(NeInt(0)))  # tpyc: ok
    print("subclass empty", bool(EmptyStr()), gb(EmptyStr()), bool(EmptyStr("x")))  # tpyc: ok
    ls = LenStr()
    print("subclass len", bool(ls), gb(ls))  # tpyc: ok
    # a wide __len__ result is compared at its own width, not narrowed
    wl = WideLenStr("payload")
    zl = ZeroLenStr("payload")
    print("subclass wide len", bool(wl), gb(wl), bool(zl), gb(zl))  # tpyc: ok
    # a StrView subclass tests its text, as a str subclass does
    v = View("payload")
    ev = View("")
    print("subclass view", bool(v), gb(v), bool(ev), gb(ev))  # tpyc: ok


def generic_method() -> None:
    p = Probe()
    print("method", p.m(0), p.m(2.5), p.m(""), p.m("x"), p.m(False))


def lambdas() -> None:
    # a lambda body calling bool() in a typed Callable slot
    f: Callable[[float], bool] = lambda v: bool(v)  # tpyc: ok
    print("lambda", f(0.0), f(-0.5))


def main() -> None:
    subclasses()
    generic_method()
    lambdas()
