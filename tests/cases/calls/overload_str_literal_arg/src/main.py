# A string LITERAL arg at an overloaded call site must bind to the
# sema-resolved overload, not be re-ranked by C++ (const char[N] -> bool
# outranks -> string_view, so a bare literal silently picked a bool overload).

from tpy import StrView, dispatch


@dispatch
def kind(x: bool) -> str:
    return "bool"
@dispatch
def kind(x: str) -> str:
    return "str"


@dispatch
def sv(x: bool) -> str:
    return "sv-bool"
@dispatch
def sv(x: StrView) -> str:
    return "sv-str"


# Generic overload: a str literal resolving here binds T by deduction, so the
# pin must be skipped (the param renders param_val_or_ref_t<T>, not a view).
@dispatch
def gen_ov[T](x: T) -> str:
    return "generic"
@dispatch
def gen_ov(x: bool) -> str:
    return "gen-bool"


def echo(x: str) -> str:
    return x


class C:
    @dispatch
    def kind(self, x: bool) -> str:
        return "m-bool"
    @dispatch
    def kind(self, x: str) -> str:
        return "m-str"


def main() -> None:
    print(kind("ok"))
    print(kind(True))
    s = "ok"
    print(kind(s))
    print(sv("v"))
    print(gen_ov("hi"))
    print(echo("hi"))
    c = C()
    print(c.kind("ok"))
    print(c.kind(True))


main()
