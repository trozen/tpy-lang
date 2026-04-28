# `case None as x:` is rejected on a union -- there is no value to bind
# because NoneType maps to std::monostate in the wrapper struct's variant.

type V = None | int | str | list[V]


def f(v: V) -> str:
    match v:
        case None as x:  # tpyc: error(/'as' binding not allowed on 'case None:'/)
            return "null"
        case int():
            return "int"
        case str():
            return "str"
        case list():
            return "list"
    return ""


def main() -> None:
    pass


main()
