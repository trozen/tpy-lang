from tpy import Int32
from mypackage.nonexistent import X  # tpyc: error(/Module 'mypackage.nonexistent' not found/)

def main() -> Int32:
    return Int32(0)

main()
