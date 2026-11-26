# A comprehension variable named like a builtin is called as itself, never
# folded to the builtin's result; the call is refused for now
# (BUGS.md#call-shadowing-import-refused), never silently `65`.
from typing import Callable
from tpy import int32


def seven(s: str) -> int32:
    return 7


def main() -> None:
    fs: list[Callable[[str], int32]] = [seven]
    # The renamed twin and the builtin fold compile.
    print([f("A") for f in fs], ord("A"))
    print([ord("A") for ord in fs])  # tpyc: error(/not yet supported by C\+\+ code generation/)


main()
