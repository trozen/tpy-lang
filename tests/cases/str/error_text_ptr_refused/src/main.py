# A Ptr[char] has no text rendering, so a user Stringable parameter refuses it
# with a diagnostic naming only Python types (one representative refusal:
# Ptr[None], an open Ptr[T] and slice refuse the same way).
from tpy import Ptr, Stringable, char, take_ptr


def text(v: Stringable) -> str:
    return str(v)


def show(p: Ptr[char]) -> str:
    return text(p)  # tpyc: error(/Type Ptr\[char\] does not conform to protocol Stringable in argument 'v'/)


def main() -> None:
    c: char = "x"
    print(show(take_ptr(c)))


main()
