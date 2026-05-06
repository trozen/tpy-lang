from a import Counter
from typing import Protocol

class Greeter(Protocol):
    def hello(self) -> str: ...

# Counter (defined in cycle peer a.py) is referenced here just to
# close the import cycle; structural conformance to Greeter is
# checked at the use site (use_proto(Counter()) in main.py).
def counter_zero() -> int:
    c: Counter = Counter()
    return 0
