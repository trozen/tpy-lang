# Error: enum member value exceeds underlying Int8 range
from enum import Enum
from tpy import Int8

class Bad(Int8, Enum):
    Big = 200  # tpyc: error(/out of range/)

def main() -> None:
    print(Bad.Big)

main()
