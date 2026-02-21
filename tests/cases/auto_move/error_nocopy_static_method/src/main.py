# Test: @nocopy type passed to static method Own[T] param -- non-last-use should error
from tpy import Int32, Own, nocopy

@nocopy
class Handle:
    fd: Int32

class Factory:
    @staticmethod
    def consume(h: Own[Handle]) -> Int32:
        return h.fd

def main() -> None:
    h: Handle = Handle()
    h.fd = 42
    result: Int32 = Factory.consume(h)  # tpyc: error(/@nocopy.*used after/)
    print(h.fd)

main()
