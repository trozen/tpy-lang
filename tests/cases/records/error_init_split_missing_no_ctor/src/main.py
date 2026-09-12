# Field with no default constructor not initialized before constructor body is an error.
from tpy import nocopy, int32

@nocopy
class Handle:
    id: int32
    def __init__(self, id: int32):
        self.id = id

class Owner:
    h: Handle    # Handle has no default ctor -> error at split point
    tag: int32   # int32 is default-constructible -> warning (not error)

    def __init__(self, tag: int32):
        self.tag = tag           # init section
        print("init")            # tpyc: error(/h.*has no default constructor/)

def main() -> None:
    o = Owner(int32(1))
    print(o.tag)

main()
