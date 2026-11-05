# `Literal[str]`-annotated local initialized from a Literal-returning function:
# a `Literal[str]` is a `str` in C++, so the call returns an owned string and
# the local owns it, exactly as a `str` local bound to a `-> str` call does.
from typing import Literal


def get_r() -> Literal["r"]:
    return "r"


def main() -> None:
    m: Literal["r", "w", "rb", "wb"] = get_r()
    print(m)


main()
