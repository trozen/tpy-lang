# bytearray.extend accepts Iterable[uint8] and Iterable[int32]; int64 values
# promote the whole list to int64 and no overload matches.
from tpy import int64


def main() -> None:
    ba = bytearray()
    xs = [1, int64(1)]
    ba.extend(xs)  # tpyc: error(/No matching overload.*extend.*int64/)


main()
