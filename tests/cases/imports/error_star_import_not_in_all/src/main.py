# hidden_fn not in __all__, so it should not be available
from tpy import int32
from helpers import *

def main() -> int32:
    result = hidden_fn(int32(1))  # tpyc: error(/hidden_fn/)
    print(result)
    return int32(0)

main()
