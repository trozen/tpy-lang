# *args with @nocopy types: passed by pointer, no copies
from tpy import nocopy, int32

@nocopy
class Resource:
    id: int32
    def __init__(self, id: int32) -> None:
        self.id = id
    def __del__(self) -> None:
        print("drop", self.id)

def use_all(*args: Resource) -> None:
    for r in args:
        print("use", r.id)

def main() -> None:
    a = Resource(1)
    b = Resource(2)
    use_all(a, b)
    print("after use_all")

main()
