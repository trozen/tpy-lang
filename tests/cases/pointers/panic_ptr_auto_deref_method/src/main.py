from tpy import Ptr, Int32

class Counter:
    value: Int32
    def __init__(self, value: Int32) -> None:
        self.value = value
    def get_value(self) -> Int32:
        return self.value

def main() -> None:
    p: Ptr[Counter] = Ptr[Counter]()
    print(p.get_value())  # tpyc: nullable(p)

main()
