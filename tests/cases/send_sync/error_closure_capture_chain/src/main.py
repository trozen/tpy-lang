# A nested def that captures a view local has a non-Send frame; passing it
# into a Send[Callable] slot must name the offending capture in the why-not
# chain (the function-reference source kind).
from tpy import int32, Send
from typing import Callable


def take(cb: Send[Callable[[int32], None]]) -> None:
    cb(1)


def main() -> None:
    s = "view"

    def handler(n: int32) -> None:
        print(s, n)

    take(handler)  # tpyc: error(/'Callable\[\[int32\], None\]' is not Send/)


main()
