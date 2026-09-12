# bytes()/bytearray() only accept Iterable[uint8] and Iterable[int32]; an
# int64 element promotes the whole list to int64 and fails to match either
# overload (int64 narrowing to uint8/int32 is not implicit).
from tpy import int64


def main() -> None:
    xs = [1, int64(1)]
    print(bytes(xs))  # tpyc: error(/bytes\(\) cannot convert.*int64/)


main()
