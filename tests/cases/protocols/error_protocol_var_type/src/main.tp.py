from typing import Sized
from tpy import Int32

def main() -> None:
    x: Sized = [1, 2, 3]  # tpyc: error(/Protocol type.*cannot be used as a variable type/)
    print(len(x))

main()
