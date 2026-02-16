# @readonly(False) on __getitem__ opts out of implicit readonly on a record
# that also has __len__ (Sized+Sequence conformance not required to be const).
from tpy import Int32, readonly

class CachingContainer:
    data: Int32
    last_access: Int32

    def __init__(self, data: Int32) -> None:
        self.data = data
        self.last_access = -1

    @readonly(False)
    def __getitem__(self, index: Int32) -> Int32:
        self.last_access = index
        return self.data

    def __len__(self) -> Int32:
        return 1

def main() -> None:
    c: CachingContainer = CachingContainer(42)
    print(c[0])
    print(c.last_access)

main()
