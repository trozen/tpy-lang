from tpy import Ptr, int32

class Counter:
    value: int32
    def __init__(self, value: int32) -> None:
        self.value = value
    def get_value(self) -> int32:
        return self.value

def main() -> None:
    p: Ptr[Counter] = Ptr[Counter]()
    print(p.get_value())  # tpyc: nullable(p)

main()
