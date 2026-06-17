# A nested def that captures a view local has a non-Send frame; passing it
# into a Send[Callable] slot must name the offending capture in the why-not
# chain (the function-reference source kind).
from tpy import Int32, Send
from typing import Callable


def take(cb: Send[Callable[[Int32], None]]) -> None:
    cb(1)


def main() -> None:
    s = "view"

    def handler(n: Int32) -> None:
        print(s, n)

    take(handler)  # tpyc: error(/'Callable\[\[Int32\], None\]' is not Send/)


main()
