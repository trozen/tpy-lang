# Move-through alias of Own[T] parameter at last use.
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32

    def __init__(self, fd: int32):
        self.fd = fd


def forward_via_alias(h: Own[Handle]) -> Own[Handle]:
    alias = h
    return alias


def main():
    r = forward_via_alias(Handle(55))
    print(r.fd)


main()
