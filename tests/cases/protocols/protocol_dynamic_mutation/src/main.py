# Mutation visibility through @dynamic protocol dispatch
# Verifies all three paths: direct inheritance, ref adapter, and local owning
from tpy import dynamic, Int32
from typing import Protocol

@dynamic
class Counter(Protocol):
    def increment(self) -> None:
        ...
    def value(self) -> Int32:
        ...

# Direct inheritor
class MyCounter(Counter):
    count: Int32
    def __init__(self) -> None:
        self.count = 0
    def increment(self) -> None:
        self.count = self.count + Int32(1)
    def value(self) -> Int32:
        return self.count

# Structural conformance (no inheritance)
class Tally:
    count: Int32
    def __init__(self) -> None:
        self.count = 0
    def increment(self) -> None:
        self.count = self.count + Int32(1)
    def value(self) -> Int32:
        return self.count

def bump(c: Counter) -> None:
    c.increment()

def main() -> None:
    # 1. Direct inheritance: mutation visible (pass by ref, implicit upcast)
    mc = MyCounter()
    bump(mc)
    print(mc.value())      # 1

    # 2. Structural conformance lvalue: mutation visible (ref adapter)
    t = Tally()
    bump(t)
    print(t.value())       # 1

    # 3. Local owning: mutation visible (modifying through pointer to owned slot)
    c: Counter = MyCounter()
    c.increment()
    c.increment()
    print(c.value())       # 2

    # 4. Local owning, structural: same behavior
    c2: Counter = Tally()
    c2.increment()
    print(c2.value())      # 1

main()
