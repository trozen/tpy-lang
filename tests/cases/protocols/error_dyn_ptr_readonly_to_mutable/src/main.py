# Ptr[readonly[Child]] -> Ptr[Awaker] must be rejected. The
# Ptr[Subclass] -> Ptr[@dynamic Protocol] coercion is covariant in the
# pointee type but cannot launder away const: a readonly source can
# only coerce to a readonly target.
from typing import Protocol
from tpy import Int32, Ptr, dynamic, nocopy, readonly


@dynamic
class Awaker(Protocol):
    def mark(self, task_id: Int32) -> None: ...


@nocopy
class Executor(Awaker):
    def __init__(self) -> None: pass
    def mark(self, task_id: Int32) -> None: pass


def aim_mut(p: Ptr[Awaker]) -> None:
    p.mark(1)


def main() -> None:
    e = Executor()
    pc: Ptr[readonly[Executor]] = e
    aim_mut(pc)  # tpyc: error(/Type mismatch in argument 'p': expected Ptr\[Awaker\], got Ptr\[readonly\[Executor\]\]/)


main()
