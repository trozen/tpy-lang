# A __getitem__ returning its reference-type key param aliases the key: the
# mutable operator[] shim mirrors the mutable clone's param signature (the
# key stays T& there because the borrow escapes through the return), so the
# subscript result is writable and mutation flows both ways, like CPython.
from tpy import int32


class Node:
    v: int32

    def __init__(self, v: int32):
        self.v = v


class KeyEcho:
    def __init__(self):
        pass

    def __getitem__(self, k: Node) -> Node:
        return k


def main():
    e = KeyEcho()
    k = Node(1)
    r = e[k]
    k.v = 5
    print(r.v)
    r.v = 9
    print(k.v)


main()
