# hidden_fn not in __all__, so it should not be available
from tpy import Int32
from helpers import *

def main() -> Int32:
    result = hidden_fn(Int32(1))  # tpyc: error(/hidden_fn/)
    print(result)
    return Int32(0)

main()
