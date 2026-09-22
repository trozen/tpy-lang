# A tuple global that OWNS a reference element is its own storage, so once a
# module-level unpack has aimed a pointer-slot target into it, a rebind would
# overwrite the object that target points at (the alias would follow the
# rebind; CPython keeps the old object). Until such a global re-points at
# fresh static backing the way the scalar reference global does
# (BUGS.md#global-tuple-ref-storage-form), it cannot be rebound after being
# borrowed from; with no alias (`t2` below) the rebind is a plain storage
# assign and stays allowed.
from tpy import int32, Own


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def mk(n: int32) -> tuple[Own[Box], int32]:
    return (Box(n), 1)


t2 = (Box(1), 1)
t2 = (Box(2), 2)  # no alias into t2: allowed
tg = mk(1)
gx, gk = tg
tg = mk(7)  # tpyc: error(/a module-level unpack borrowed an element of it/)
print(gx.n, t2[0].n)
