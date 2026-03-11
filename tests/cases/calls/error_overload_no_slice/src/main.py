# Error: slicing a user type without __getitem__(slice) overload
from tpy import Int32

class MyList:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = [Int32(10), Int32(20)]

    def __getitem__(self, index: Int32) -> Int32:
        return self._data[index]

def main() -> None:
    m = MyList()
    sp = m[Int32(0):Int32(1)]  # tpyc: error(/Slicing is not supported/)

main()
