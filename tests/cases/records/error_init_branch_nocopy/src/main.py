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
        if flag:  # tpyc: error(/has no default constructor/)
            self.h = Handle(1)  # not reported (error already raised at split point)
        else:
            self.h = Handle(2)  # not reported (error already raised at split point)

def main() -> None:
    w = Wrapper(True)
    print(w.h.id)

main()
