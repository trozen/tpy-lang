# A narrowed `str | None` / `bytes | None` param (borrow form
# optional<string_view> / optional<span>) dereferences to a view. A str local
# bound to it stays a zero-copy view; a bytes local converts the span to owned
# bytes (span->vector is not implicit the way string_view->string is).


def str_single(a: str | None) -> None:
    if a is not None:
        x = a  # tpyc: type(StrView)
        print(x)


def str_compound(a: str | None, b: str) -> None:
    if a is not None:
        x = a if len(b) > 0 else b  # tpyc: type(StrView)
        print(x)


def bytes_compound(a: bytes | None, b: bytes) -> None:
    # bytes Optional param is borrow-form (span); the local converts to owned.
    if a is not None:
        y = a if len(b) > 0 else b  # tpyc: type(bytes)
        print(len(y))


def main() -> None:
    str_single("solo")
    str_compound("then", "x")
    bytes_compound(b"abcd", b"ef")


main()
