# readonly[Int32] is a no-op -- value types are copies, so readonly is stripped.
from tpy import Int32, readonly

def add_one(x: readonly[Int32]) -> Int32:
    return x + Int32(1)

def main() -> None:
    v = Int32(10)
    print(add_one(v))

main()
