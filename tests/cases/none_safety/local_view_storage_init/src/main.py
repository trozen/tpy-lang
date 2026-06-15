# A str/bytes local initialized from a borrow-form view expression (incl. an
# Optional-param ternary) converts to owned storage, same rule as the return
# boundary -- previously the Optional-param ternary failed the C++ build.
from typing import Optional


def join_or(a: Optional[str], b: str) -> int:
    x: str = a if a is not None else b
    return len(x)


def bytes_or(a: Optional[bytes], b: bytes) -> int:
    y: bytes = a if a is not None else b
    return len(y)


def passthrough_str(s: str) -> int:
    local: str = s
    return len(local)


def passthrough_bytes(b: bytes) -> int:
    local: bytes = b
    return len(local)


def main() -> None:
    print(join_or("hello", "z"), join_or(None, "zz"))
    print(bytes_or(b"abcd", b"z"), bytes_or(None, b"zz"))
    print(passthrough_str("hi"), passthrough_bytes(b"xyz"))


main()
