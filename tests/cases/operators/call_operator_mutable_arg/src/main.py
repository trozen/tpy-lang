# An arg-mutating __call__ works through the operator() shim: the shim's
# params mirror the method's inferred const-ness (a mutated param stays T&),
# so the callable-object sugar and the caller-visible mutation match CPython.
from tpy import Int32


class Node:
    v: Int32

    def __init__(self, v: Int32):
        self.v = v


class Bumper:
    def __init__(self):
        pass

    def __call__(self, x: Node) -> Int32:
        x.v += 1
        return x.v


def main():
    b = Bumper()
    n = Node(1)
    print(b(n))
    print(n.v)
    print(b(n), n.v)


main()
