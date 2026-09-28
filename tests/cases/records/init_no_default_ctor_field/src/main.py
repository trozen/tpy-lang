# A field whose type needs `__init__` arguments, initialized in the init section; Handle
# still gets the C++ placeholder `Handle() = default;` (its fields are all trivial).
from tpy import nocopy, int32

@nocopy
class Handle:
    id: int32
    def __init__(self, id: int32):
        self.id = id

class Owner:
    h: Handle
    tag: int32

    def __init__(self, id: int32, tag: int32):
        self.h = Handle(id)   # init section: h assigned
        self.tag = tag        # init section: tag assigned

def main() -> None:
    o = Owner(int32(1), int32(42))
    print(o.h.id)
    print(o.tag)

main()
