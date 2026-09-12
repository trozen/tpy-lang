from tpy import Ptr, int32, readonly

class Counter:
    value: int32
    def __init__(self, value: int32) -> None:
        self.value = value
    def __len__(self) -> int32:
        return self.value

def main() -> None:
    p: Ptr[readonly[Counter]] = Ptr[readonly[Counter]]()
    print(p.__len__())  # tpyc: nullable(p)

main()
