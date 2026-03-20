# Test error: aug-assign to outer variable without nonlocal
from tpy import Int32

def main() -> None:
    x: Int32 = 0
    def f() -> None:
        x += 1  # tpyc: error(/Cannot modify.*without.*nonlocal/)
    f()

main()
