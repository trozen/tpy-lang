# Error: slicing a user type without __getitem__(slice) overload
from tpy import int32

class MyList:
    _data: list[int32]

    def __init__(self) -> None:
        self._data = [int32(10), int32(20)]

    def __getitem__(self, index: int32) -> int32:
        return self._data[index]

def main() -> None:
    m = MyList()
    sp = m[int32(0):int32(1)]  # tpyc: error(/Slicing is not supported/)

main()
