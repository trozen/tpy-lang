# bytearray.extend accepts Iterable[UInt8] and Iterable[Int32]; Int64 values
# promote the whole list to Int64 and no overload matches.
from tpy import Int64


def main() -> None:
    ba = bytearray()
    xs = [1, Int64(1)]
    ba.extend(xs)  # tpyc: error(/No matching overload.*extend.*Int64/)


main()
