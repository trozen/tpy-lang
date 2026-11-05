# chr(n) / char(n) over the ASCII range they accept (0..127), at int32 and
# int, in a free function, a constructor, a method, a comprehension, a
# module constant and a class constant.
from typing import Final
from tpy import char

# Module constant: char(65) stays a compile-time constant.
LETTER: Final[char] = char(65)


class Glyphs:
    # Class constant: char(65) is a static constexpr member.
    MARK: Final[char] = char(65)  # tpyc: ok


class Alphabet:
    def __init__(self, base: int) -> None:
        self.base = base

    def letter(self, k: int) -> str:
        # Method: chr at int (BigInt).
        return chr(self.base + k)  # tpyc: ok


def free_function() -> None:
    n = 97
    # Free function: chr at int32, then at int.
    print("free:", repr(chr(n)), repr(chr(0)), repr(chr(127)))  # tpyc: ok
    big: int = 90
    print("free:", repr(chr(big)))  # tpyc: ok


def constructor() -> None:
    n = 48
    big: int = 57
    # char(n) constructor at int32 and at int.
    print("ctor:", repr(char(n)), repr(char(big)))  # tpyc: ok


def comprehension() -> None:
    # Comprehension: chr over a code-point range.
    letters = [chr(c) for c in range(65, 70)]  # tpyc: ok
    print("comp:", letters)


def main() -> None:
    free_function()
    constructor()
    print("method:", Alphabet(65).letter(2))
    comprehension()
    print("const:", LETTER)
    print("class_const:", Glyphs.MARK)


main()
