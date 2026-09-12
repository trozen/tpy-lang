# Error: enum member value exceeds underlying int8 range
from enum import Enum
from tpy import int8

class Bad(int8, Enum):
    Big = 200  # tpyc: error(/out of range/)

def main() -> None:
    print(Bad.Big)

main()
