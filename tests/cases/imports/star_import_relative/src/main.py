# Relative star import from sibling within a package
from tpy import Int32
from pkg.consumer import compute

def main() -> Int32:
    result = compute()
    print(result)
    return Int32(0)

main()
