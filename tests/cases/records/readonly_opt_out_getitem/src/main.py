# @readonly(False) on __getitem__ opts out of implicit readonly on a record
# that also has __len__ (Sized+Sequence conformance not required to be const).
from tpy import int32, readonly

class CachingContainer:
    data: int32
    last_access: int32

    def __init__(self, data: int32) -> None:
        self.data = data
        self.last_access = -1

    @readonly(False)
    def __getitem__(self, index: int32) -> int32:
        self.last_access = index
        return self.data

    def __len__(self) -> int32:
        return 1

def main() -> None:
    c: CachingContainer = CachingContainer(42)
    print(c[0])
    print(c.last_access)

main()
