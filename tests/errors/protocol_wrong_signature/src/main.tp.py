from typing import Sized
from tpy import Int32

class WrongReturn:
    value: Int32

    def __len__(self) -> int:  # Returns BigInt instead of Int32
        return self.value

def count(items: Sized) -> Int32:
    return len(items)

def main() -> None:
    x: WrongReturn = WrongReturn()
    print(count(x))  # tpyc: error(/does not conform to protocol/)

main()
