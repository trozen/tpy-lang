# __copy__ on a type with __del__: each copy owns its own resource, no double-free
from __future__ import annotations
from tpy import Int32, Own, copy


class Resource:
    id: Int32

    def __init__(self, id: Int32):
        self.id = id
        print("alloc", id)

    def __del__(self):
        print("free", self.id)

    def __copy__(self) -> Own[Resource]:
        return Resource(self.id + 100)


def main():
    r1 = Resource(1)
    r2 = copy(r1)
    print("r1 =", r1.id)
    print("r2 =", r2.id)


main()
