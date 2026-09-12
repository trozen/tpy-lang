# Repetition copies the element into every slot, so a @nocopy element must be
# rejected in sema (previously a raw C++ template error).
from tpy import int32, nocopy


@nocopy
class Handle:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def main():
    hs = [Handle(1)] * 3  # tpyc: error(/Cannot repeat an element of non-copyable type Handle/)
    print(hs[0].v)


main()
