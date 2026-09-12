from typing import Sized
from tpy import int32

class WrongReturn:
    value: str

    def __len__(self) -> str:  # Returns str instead of int32
        return self.value

def count(items: Sized) -> int32:
    return len(items)

def main() -> None:
    x: WrongReturn = WrongReturn()
    print(count(x))  # tpyc: error(/does not conform to protocol/)

main()
