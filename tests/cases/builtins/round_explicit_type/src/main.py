# Test round[T](x) with explicit type parameter subscript
from tpy import int32, int64

def main() -> None:
    # Explicit type parameter
    a: int64 = round[int64](7.7)
    print(a)

    b: int32 = round[int32](2.5)
    print(b)

    # round[T] with default_int inference
    c: int64 = round(99.9)
    print(c)

main()
