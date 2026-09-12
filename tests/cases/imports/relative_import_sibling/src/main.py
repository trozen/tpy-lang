from tpy import int32
from mypackage.consumer import compute

def main() -> int32:
    result: int32 = compute()
    print(result)
    return int32(0)

main()
