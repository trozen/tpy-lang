# Regression: same-module ValueType used inside an inline method body
# (which instantiates a template parameterised on the ValueType). The
# `is_value_type` spec must precede the instantiation -- previously it
# emitted at file end, causing "specialization after instantiation".
from tpy import int32, ValueType
import heapq


class TimerEntry(ValueType):
    deadline: float
    tid: int32

    def __init__(self, deadline: float, tid: int32) -> None:
        self.deadline = deadline
        self.tid = tid

    def __lt__(self, o: 'TimerEntry') -> bool:
        return self.deadline < o.deadline


class Owner:
    heap: list[TimerEntry]

    def __init__(self) -> None:
        self.heap = []

    def add(self, d: float, t: int32) -> None:
        heapq.heappush(self.heap, TimerEntry(d, t))


def main() -> None:
    o = Owner()
    o.add(2.0, 20)
    o.add(1.0, 10)
    print(o.heap[0].deadline)


main()
