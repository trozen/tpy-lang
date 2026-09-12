# Relative star import from sibling within a package
from tpy import int32
from pkg.consumer import compute

def main() -> int32:
    result = compute()
    print(result)
    return int32(0)

main()
