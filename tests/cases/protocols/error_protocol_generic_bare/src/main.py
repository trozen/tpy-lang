from typing import Sequence
from tpy import int32

def bad(items: Sequence) -> int32:  # tpyc: error(/Generic protocol.*requires type arguments/)
    return len(items)

def main() -> None:
    pass

main()
