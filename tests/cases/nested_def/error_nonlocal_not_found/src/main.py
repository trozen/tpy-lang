# Test error: nonlocal name not in outer scope
from tpy import Int32

def main() -> None:
    def inner() -> None:
        nonlocal z  # tpyc: error(/No binding for nonlocal/)
        z = 10
    inner()

main()
