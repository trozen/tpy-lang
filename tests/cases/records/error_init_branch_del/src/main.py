# Field with __del__ assigned inside control flow in __init__ is an error.
from tpy import Int32

class Resource:
    id: Int32
    def __init__(self, id: Int32):
        self.id = id
    def __del__(self):
        print("close", self.id)

class Owner:
    r: Resource
    def __init__(self, flag: bool):
        if flag:  # tpyc: error(/has no default constructor/)
            self.r = Resource(1)  # not reported (error already raised at split point)
        else:
            self.r = Resource(2)  # not reported (error already raised at split point)

def main() -> None:
    o = Owner(True)
    print(o.r.id)

main()
