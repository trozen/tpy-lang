# isinstance() on protocol with concrete type -- should error
from typing import Sized
from tpy import Int32

def main() -> None:
    nums: list[Int32] = [1, 2, 3]
    if isinstance(nums, Sized):  # tpyc: error(/protocol-typed parameter/)
        print("sized")

main()
