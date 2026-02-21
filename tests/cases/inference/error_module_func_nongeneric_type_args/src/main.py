# Type args on non-generic module function should be rejected.
from tpy import Int32
import helpers

def main() -> None:
    y = helpers.inc[Int32](Int32(1))  # tpyc: error(/not generic/)

main()
