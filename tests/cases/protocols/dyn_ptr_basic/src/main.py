# Ptr[P] for @dynamic protocol P -- non-owning polymorphic reference.
# Covers two coercion shapes:
#   * record -> Ptr[@dynamic Protocol]                (address-of upcast at arg/init site)
#   * record -> Ptr[readonly[@dynamic Protocol]]      (readonly target variant)
from typing import Protocol
from tpy import Int32, Ptr, dynamic, nocopy, readonly


@dynamic
class Awaker(Protocol):
    def mark(self, task_id: Int32) -> None: ...


@nocopy
class ExecutorA(Awaker):
    log: list[Int32]
    def __init__(self) -> None:
        self.log = []
    def mark(self, task_id: Int32) -> None:
        self.log.append(task_id * 10)


@nocopy
class ExecutorB(Awaker):
    log: list[Int32]
    def __init__(self) -> None:
        self.log = []
    def mark(self, task_id: Int32) -> None:
        self.log.append(task_id + 1000)


@nocopy
class Notifier:
    awaker: Ptr[Awaker]
    task_id: Int32

    def __init__(self) -> None:
        self.awaker = None  # Ptr[T] is implicitly nullable
        self.task_id = 0

    def aim(self, p: Ptr[Awaker], tid: Int32) -> None:
        self.awaker = p
        self.task_id = tid

    def fire(self) -> None:
        if self.awaker is None:
            return
        self.awaker.mark(self.task_id)


# record -> Ptr[Awaker] direct upcast (UPCAST_TO_PTR path). Takes a
# CONCRETE-record param so the coerce happens at the call site, not
# inside the body.
def fire_direct(exec: ExecutorA, tid: Int32) -> None:
    n = Notifier()
    n.aim(exec, tid)  # record exec -> Ptr[Awaker] arg
    n.fire()


def main() -> None:
    a = ExecutorA()
    b = ExecutorB()
    n = Notifier()

    # record -> Ptr[Awaker] direct upcast at the arg site (UPCAST_TO_PTR).
    n.aim(a, 5)
    n.fire()

    # Same upcast works for ExecutorB -- polymorphic dispatch.
    n.aim(b, 7)
    n.fire()

    # Same upcast through a function-param boundary.
    fire_direct(a, 11)

    # record -> Ptr[readonly[Awaker]] readonly-target upcast (sema-only;
    # protocol's mark() isn't @readonly so we can't dispatch through a
    # readonly pointer, but the coercion must compile).
    ro_ptr: Ptr[readonly[Awaker]] = a  # tpyc: ok

    print(a.log)
    print(b.log)
    print(ro_ptr is not None)


main()
