# Returning a borrow-form bytes param (span) converts to the owned bytes
# storage slot, parallel to the str view->string return -- including the
# Optional[bytes]-param ternary the str path also covers.
from typing import Optional


def echo(b: bytes) -> bytes:
    return b


def first_or_empty(b: bytes) -> bytes:
    if len(b) == 0:
        return b"empty"
    return b


def opt_or_default(b: Optional[bytes]) -> bytes:
    return b if b is not None else b"none"


def main() -> None:
    r = echo(b"hello")
    print(len(r), r == b"hello")
    print(len(first_or_empty(b"")))
    print(opt_or_default(b"xy") == b"xy", opt_or_default(None) == b"none")


main()
