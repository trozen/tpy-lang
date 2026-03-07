# Error: dict comprehension over non-iterable type
from tpy import Int32

def main() -> None:
    x: Int32 = 5
    d = {i: i for i in x}  # tpyc: error(/Cannot iterate/)

main()
