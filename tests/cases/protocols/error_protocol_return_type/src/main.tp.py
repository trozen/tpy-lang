from typing import Sized
from tpy import Int32

def get_sized() -> Sized:  # tpyc: error(/Protocol type.*cannot be used as a return type/)
    return [1, 2, 3]

def main() -> None:
    pass

main()
