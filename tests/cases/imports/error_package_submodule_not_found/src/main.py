from tpy import int32
from mypackage.nonexistent import X  # tpyc: error(/Module 'mypackage.nonexistent' not found/)

def main() -> int32:
    return int32(0)

main()
