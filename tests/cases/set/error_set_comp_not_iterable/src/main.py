# Error: set comprehension over non-iterable type
from tpy import int32

def main() -> None:
    x: int32 = 5
    s = {i for i in x}  # tpyc: error(/Cannot iterate/)

main()
