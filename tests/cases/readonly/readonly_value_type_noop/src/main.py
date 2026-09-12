# readonly[int32] is a no-op -- value types are copies, so readonly is stripped.
from tpy import int32, readonly

def add_one(x: readonly[int32]) -> int32:
    return x + int32(1)

def main() -> None:
    v = int32(10)
    print(add_one(v))

main()
