from typing import Self
from tpy import int32

def bad(x: Self) -> Self:  # tpyc: error(/Self type cannot be used/)
    return x

def main() -> None:
    pass

main()
