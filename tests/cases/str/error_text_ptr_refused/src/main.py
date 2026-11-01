# An opaque Ptr[None] has no pointee to render, so a user Stringable parameter
# refuses it with a diagnostic naming only Python types (one representative
# refusal: slice refuses the same way).
from tpy import Ptr, Stringable


def text(v: Stringable) -> str:
    return str(v)


def show(p: Ptr[None]) -> str:
    return text(p)  # tpyc: error(/Type Ptr\[None\] does not conform to protocol Stringable in argument 'v'/)


def main() -> None:
    p: Ptr[None] = None
    print(show(p))


main()
