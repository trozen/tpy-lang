# @nosend on a free function forces its frame non-Send: it no longer
# converts into a Send[Callable[...]] slot.
from tpy import Int32, Send, nosend
from typing import Callable

@nosend
def plain(n: Int32) -> None:
    print("plain", n)

def take(cb: Send[Callable[[Int32], None]]) -> None:
    cb(1)

def main() -> None:
    take(plain)  # tpyc: error(/'Callable\[\[Int32\], None\]' is not Send/)

main()
