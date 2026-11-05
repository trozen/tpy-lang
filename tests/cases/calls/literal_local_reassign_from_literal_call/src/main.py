# `Literal[str]`-annotated local reassigned from a Literal-returning function:
# the call returns an owned string (a `Literal[str]` is a `str` in C++), so
# the local owns its storage, exactly as its `str` twin reassigned from a
# `-> str` call does.
from typing import Literal


def get_r() -> Literal["r"]:
    return "r"


def main() -> None:
    m: Literal["r", "w", "rb", "wb"] = "r"
    print(m)
    m = get_r()
    print(m)


main()
