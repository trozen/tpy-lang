# Error: generator expression source must be iterable
from tpy import int32

def main() -> None:
    x: int32 = 5
    result = (i for i in x)  # tpyc: error(/[Cc]annot iterate/)

main()
