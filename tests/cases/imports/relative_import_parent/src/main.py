from tpy import int32
from outer.inner.consumer import compute

def main() -> int32:
    result: int32 = compute()
    print(result)
    return int32(0)

main()
