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
        if flag:
            self.r = Resource(1)  # tpyc: error(/not safely default-constructible/)
        else:
            self.r = Resource(2)  # tpyc: ok

def main() -> None:
    o = Owner(True)
    print(o.r.id)

main()
