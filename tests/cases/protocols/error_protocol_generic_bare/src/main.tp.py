from typing import Sequence
from tpy import Int32

def bad(items: Sequence) -> Int32:  # tpyc: error(/Generic protocol.*requires type arguments/)
    return len(items)

def main() -> None:
    pass

main()
