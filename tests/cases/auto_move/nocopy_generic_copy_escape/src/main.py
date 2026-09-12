# __copy__ on generic record suppresses nocopy propagation from type args
from __future__ import annotations
from tpy import int32, Own, nocopy, copy


@nocopy
class Handle:
    fd: int32

    def __init__(self, fd: int32):
        self.fd = fd


class TaggedValue[T]:
    data: int32

    def __init__(self, data: int32):
        self.data = data

    def __copy__(self) -> Own[TaggedValue[T]]:
        return TaggedValue[T](self.data)


def main():
    v = TaggedValue[Handle](42)
    v2 = copy(v)  # tpyc: ok
    print(v.data)
    print(v2.data)


main()
