# __copy__ on child class with parent __del__: each copy gets its own resource
from __future__ import annotations
from tpy import int32, Own, copy


class Resource:
    id: int32

    def __init__(self, id: int32):
        self.id = id
        print("alloc", id)

    def __del__(self):
        print("free", self.id)


class CopyableResource(Resource):
    def __init__(self, id: int32):
        super().__init__(id)

    def __copy__(self) -> Own[CopyableResource]:
        return CopyableResource(self.id + 100)


def main():
    r1 = CopyableResource(1)
    r2 = copy(r1)
    print("r1 =", r1.id)
    print("r2 =", r2.id)


main()
