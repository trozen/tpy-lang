# Reassigning a parameter whose C++ slot is a const reference or a view
# (`str`, `bytes`, `int`/BigInt, `String`) inside a GENERATOR. A plain
# function hoists a mutable owned copy into its prologue; a resumable frame
# needs no prologue at all -- the skeleton spells the captured member owned
# (`std::string s;`), so the reassign is a plain member write that survives
# each yield. Every section reassigns BEFORE the first yield and again
# between yields, so a lost write shows in the second value.
from typing import Iterator

from tpy import String, int32


def gs(s: str, n: int32) -> Iterator[str]:
    s = s + "!"  # tpyc: ok
    for i in range(n):
        yield s
        s = s + "?"


def gb(b: bytes, n: int32) -> Iterator[int32]:
    b = b + b"z"  # tpyc: ok
    for i in range(n):
        yield len(b)
        b = b + b"z"


def gi(v: int, n: int32) -> Iterator[int]:
    v = v + 1  # tpyc: ok
    for i in range(n):
        yield v
        v = v * 2


def gstr(s: String, n: int32) -> Iterator[str]:
    s = s + String("!")  # tpyc: ok
    for i in range(n):
        yield str(s)


class Box:
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag

    # method generator: the same reassign with a receiver in the frame
    def walk(self, s: str, n: int32) -> Iterator[str]:
        s = self.tag + s  # tpyc: ok
        for i in range(n):
            yield s
            s = s + "."


def main() -> None:
    for v in gs("a", 2):
        print("gs", v)
    for c in gb(b"ab", 2):
        print("gb", c)
    for w in gi(1, 3):
        print("gi", w)
    for t in gstr(String("x"), 2):
        print("gstr", t)
    b = Box("T")
    for u in b.walk("m", 2):
        print("walk", u)


main()
