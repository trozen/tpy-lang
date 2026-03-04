# Test round[T](x) with explicit type parameter subscript
from tpy import Int32, Int64

def main() -> None:
    # Explicit type parameter
    a: Int64 = round[Int64](7.7)
    print(a)

    b: Int32 = round[Int32](2.5)
    print(b)

    # round[T] with default_int inference
    c: Int64 = round(99.9)
    print(c)

main()
