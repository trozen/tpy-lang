# char used without import should give a clear error.
def main() -> None:
    c = char("A")  # tpyc: error(/requires: from tpy import char/)

main()
