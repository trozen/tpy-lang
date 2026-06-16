# A narrowed `str | None` param (lowered to std::optional<std::string_view>)
# dereferences to a view, so a local bound to it stays zero-copy (no double-wrap);
# a `bytes | None` param owns its buffer (optional<vector>), so its deref is owned.


def str_single(a: str | None) -> None:
    if a is not None:
        x = a  # tpyc: type(StrView)
        print(x)


def str_compound(a: str | None, b: str) -> None:
    if a is not None:
        x = a if len(b) > 0 else b  # tpyc: type(StrView)
        print(x)


def bytes_compound(a: bytes | None, b: bytes) -> None:
    # bytes Optional param owns its buffer -> stays owned (no dangling span).
    if a is not None:
        y = a if len(b) > 0 else b  # tpyc: type(bytes)
        print(len(y))


def main() -> None:
    str_single("solo")
    str_compound("then", "x")
    bytes_compound(b"abcd", b"ef")


main()
