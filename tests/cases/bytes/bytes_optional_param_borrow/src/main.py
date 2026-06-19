# A `bytes | None` param lowers to borrow form optional<span> (like str|None);
# exercises the view->owned copy at every owned sink it flows into.
from typing import Iterator


def first_or_empty(b: bytes | None) -> bytes:
    if b is None:
        return b""
    return b                      # owned-bytes return from borrow param


def collect(b: bytes | None) -> int:
    out: list[bytes] = []
    if b is not None:
        out.append(b)             # container insert from borrow param
    return len(out)


def reassigned(b: bytes | None) -> int:
    if b is None:
        b = b"fallback"           # reassign of a borrow-form param
    return len(b)


def reassigned_plain(b: bytes, c: bool) -> int:
    if c:
        b = b"longer"             # reassign of a plain bytes (span) param -> owned copy
    return len(b)


def forward(data: bytes) -> int:
    return len(first_or_empty(data))   # real bytes value into bytes|None param


class Holder:
    data: bytes

    def __init__(self):
        self.data = b""

    def store(self, b: bytes | None) -> None:
        if b is not None:
            self.data = b         # narrowed bytes|None param -> bare bytes field


def gen(b: bytes | None) -> Iterator[int]:
    # simple generator (no await) owning the bytes|None param across yields
    yield 1
    if b is not None:
        yield int(b[0])           # reads buffer after a yield -> needs owned capture


def main() -> None:
    print(len(first_or_empty(b"hello")))
    print(len(first_or_empty(None)))
    print(collect(b"xy"))
    print(reassigned(None))
    print(reassigned_plain(b"ab", True), reassigned_plain(b"abc", False))
    print(forward(b"world"))
    h = Holder()
    h.store(b"abc")
    print(len(h.data))
    total = 0
    for v in gen(b"Q"):
        total += v
    print(total)


main()
