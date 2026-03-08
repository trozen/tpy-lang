# Error: generator expression source must be iterable
from tpy import Int32

def main() -> None:
    x: Int32 = 5
    result = (i for i in x)  # tpyc: error(/[Cc]annot iterate/)

main()
