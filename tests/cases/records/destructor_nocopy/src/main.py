# Tests @nocopy combined with __del__: copy ops come from __del__ (not @nocopy's default move),
# and the drop flag prevents double-drop after move.
from tpy import nocopy, Own, Int32

@nocopy
class Handle:
    id: Int32
    def __init__(self, id: Int32):
        self.id = id
    def __del__(self):
        print("close", self.id)

def consume(h: Own[Handle]) -> None:
    print("use", h.id)

def main():
    h = Handle(1)
    consume(h)
    print("---")

    consume(Handle(2))
    print("done")

main()
