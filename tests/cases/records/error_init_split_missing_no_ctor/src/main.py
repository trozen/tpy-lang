# Field with no default constructor not initialized before constructor body is an error.
from tpy import nocopy, Int32

@nocopy
class Handle:
    id: Int32
    def __init__(self, id: Int32):
        self.id = id

class Owner:
    h: Handle    # Handle has no default ctor -> error at split point
    tag: Int32   # Int32 is default-constructible -> warning (not error)

    def __init__(self, tag: Int32):
        self.tag = tag           # init section
        print("init")            # tpyc: error(/h.*has no default constructor/)

def main() -> None:
    o = Owner(Int32(1))
    print(o.tag)

main()
