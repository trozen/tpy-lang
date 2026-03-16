# Char used without import should give a clear error.
def main() -> None:
    c = Char("A")  # tpyc: error(/requires: from tpy import Char/)

main()
