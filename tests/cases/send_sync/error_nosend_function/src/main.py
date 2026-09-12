# @nosend on a free function forces its frame non-Send: it no longer
# converts into a Send[Callable[...]] slot.
from tpy import int32, Send, nosend
from typing import Callable

@nosend
def plain(n: int32) -> None:
    print("plain", n)

def take(cb: Send[Callable[[int32], None]]) -> None:
    cb(1)

def main() -> None:
    take(plain)  # tpyc: error(/'Callable\[\[int32\], None\]' is not Send/)

main()
