# isinstance() on protocol with concrete type -- should error
from typing import Sized
from tpy import int32

def main() -> None:
    nums: list[int32] = [1, 2, 3]
    if isinstance(nums, Sized):  # tpyc: error(/protocol-typed parameter/)
        print("sized")

main()
