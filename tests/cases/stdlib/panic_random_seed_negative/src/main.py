# random.seed(n) panics on negative Int32: the module accepts non-negative
# seeds only (documented v1 constraint). Negative n hits the Int32 -> UInt32
# coercion and panics. CPython accepts negatives (via abs); TPy does not.
import random
from tpy import Int32

def main() -> None:
    random.seed(Int32(-1))

main()
