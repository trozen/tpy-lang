# bytes()/bytearray() only accept Iterable[UInt8] and Iterable[Int32]; an
# Int64 element promotes the whole list to Int64 and fails to match either
# overload (Int64 narrowing to UInt8/Int32 is not implicit).
from tpy import Int64


def main() -> None:
    xs = [1, Int64(1)]
    print(bytes(xs))  # tpyc: error(/bytes\(\) cannot convert.*Int64/)


main()
