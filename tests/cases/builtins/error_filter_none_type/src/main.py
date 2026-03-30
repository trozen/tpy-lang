# Error: filter(None, ...) with wrong first argument type (not None, not Fn)
from tpy import Int32

def main() -> None:
    filter(42, [1, 2, 3])  # tpyc: error(/No matching overload/)

main()
