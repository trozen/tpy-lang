# Ptr[P] for @dynamic protocol P -- non-owning polymorphic reference.
# Two distinct implementations dispatch through a single Waker-like
# field that holds Ptr[Awaker]; the &-of cpp_template provides the
# "addressof" primitive.
from typing import Protocol
from tpy import Int32, Ptr, dynamic, nocopy
from tpy.extern import cpp_template


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


@cpp_template("(&({0}))")
def addr_of(obj: Awaker) -> Ptr[Awaker]: ...


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


def main() -> None:
    a = ExecutorA()
    b = ExecutorB()
    n = Notifier()

    n.aim(addr_of(a), 5)
    n.fire()

    n.aim(addr_of(b), 7)
    n.fire()

    print(a.log)
    print(b.log)


main()
