# A lambda capturing a view local (StrView aliases the originating buffer)
# has a non-Send frame -- rejected at the Send[Callable] conversion.
from tpy import Int32, Send
from typing import Callable

def take(cb: Send[Callable[[Int32], None]]) -> None:
    cb(1)

def main() -> None:
    s = "view"
    take(lambda n: print(s, n))  # tpyc: error(/'Callable\[\[Int32\], None\]' is not Send/)

main()
