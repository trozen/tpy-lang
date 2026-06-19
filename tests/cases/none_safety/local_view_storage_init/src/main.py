# A str/bytes local from a borrow-form view expression (incl. an Optional-param
# ternary) compiles and runs. Both `str | None` and `bytes | None` are borrow
# form (optional<string_view> / optional<span>); a str local stays a zero-copy
# view, a bytes local converts the span to owned storage (no implicit span->vector).
from typing import Optional


def join_or(a: Optional[str], b: str) -> int:
    x: str = a if a is not None else b  # tpyc: type(StrView)
    return len(x)


def bytes_or(a: Optional[bytes], b: bytes) -> int:
    y: bytes = a if a is not None else b  # tpyc: type(bytes)
    return len(y)


def passthrough_str(s: str) -> int:
    local: str = s  # tpyc: type(StrView)
    return len(local)


def passthrough_bytes(b: bytes) -> int:
    local: bytes = b  # tpyc: type(BytesView)
    return len(local)


def main() -> None:
    print(join_or("hello", "z"), join_or(None, "zz"))
    print(bytes_or(b"abcd", b"z"), bytes_or(None, b"zz"))
    print(passthrough_str("hi"), passthrough_bytes(b"xyz"))


main()
