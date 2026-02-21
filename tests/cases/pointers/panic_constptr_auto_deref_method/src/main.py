from tpy import ConstPtr, Int32

class Counter:
    value: Int32
    def __init__(self, value: Int32) -> None:
        self.value = value
    def __len__(self) -> Int32:
        return self.value

def main() -> None:
    p: ConstPtr[Counter] = ConstPtr[Counter]()
    print(p.__len__())

main()
