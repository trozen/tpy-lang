from typing import Sized
from tpy import Int32

class NoLen:
    value: Int32

def count(items: Sized) -> Int32:
    return len(items)

def main() -> None:
    x: NoLen = NoLen()
    print(count(x))  # tpyc: error(/does not conform to protocol/)

main()
