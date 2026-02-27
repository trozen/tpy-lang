from tpy import ReadOnlyPtr, Int32

class Counter:
    value: Int32
    def __init__(self, value: Int32) -> None:
        self.value = value
    def __len__(self) -> Int32:
        return self.value

def main() -> None:
    p: ReadOnlyPtr[Counter] = ReadOnlyPtr[Counter]()
    print(p.__len__())

main()
