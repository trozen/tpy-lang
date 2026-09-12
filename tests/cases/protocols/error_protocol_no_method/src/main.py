from typing import Sized
from tpy import int32

class NoLen:
    value: int32

def count(items: Sized) -> int32:
    return len(items)

def main() -> None:
    x: NoLen = NoLen()
    print(count(x))  # tpyc: error(/does not conform to protocol/)

main()
