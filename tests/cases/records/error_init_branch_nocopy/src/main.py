# @nocopy field assigned inside control flow in __init__ is an error.
from tpy import nocopy, Int32

@nocopy
class Handle:
    id: Int32
    def __init__(self, id: Int32):
        self.id = id

class Wrapper:
    h: Handle
    def __init__(self, flag: bool):
        if flag:
            self.h = Handle(1)  # tpyc: error(/not safely default-constructible/)
        else:
            self.h = Handle(2)  # tpyc: ok

def main() -> None:
    w = Wrapper(True)
    print(w.h.id)

main()
