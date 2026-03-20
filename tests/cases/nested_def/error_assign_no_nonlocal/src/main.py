# Test error: plain assignment to outer variable without nonlocal
from tpy import Int32

def main() -> None:
    x: Int32 = 10
    def inner() -> None:
        x = 20  # tpyc: error(/Cannot assign.*without.*nonlocal/)
    inner()

main()
