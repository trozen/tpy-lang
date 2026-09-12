# Class with a non-Python-default-constructible field, properly initialized in init section.
# Handle() = default; is emitted (its C++ fields are all trivial) even though Handle requires
# an id argument in Python -- known semantic gap, see CONSTRUCTOR_DESIGN.md open question 4.
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
