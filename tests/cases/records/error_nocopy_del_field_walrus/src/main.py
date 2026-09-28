# A walrus in a field initializer binds a constructor-body local, so the init
# runs in the body; a field with no default constructor cannot wait for it.
from tpy import int32, nocopy


@nocopy
class Resource:
    id: int32

    def __init__(self, id: int32) -> None:
        self.id = id

    def __del__(self) -> None:
        print("drop", self.id)


class Holder:
    _r: Resource

    def __init__(self, seed: int32) -> None:
        self._r = Resource((n := seed))  # tpyc: error(/binds a local \(`:=`\)/)
        print(n)


def main() -> None:
    h = Holder(1)
    print(h._r.id)


main()
