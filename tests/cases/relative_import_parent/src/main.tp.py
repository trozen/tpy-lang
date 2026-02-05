from tpy import Int32
from outer.inner.consumer import compute

def main() -> Int32:
    result: Int32 = compute()
    print(result)
    return Int32(0)

main()
