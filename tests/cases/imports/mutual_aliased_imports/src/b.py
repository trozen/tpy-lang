from a import A as Other
from tpy import Int32

class Helper:
    def __init__(self) -> None: pass
    def work(self) -> Int32:
        return 5
    def caller(self, x: Other) -> Int32:
        # Uses the cycle-peer record under its alias and dispatches
        # a method call on it -- exercises both alias resolution and
        # cross-peer method dispatch through the aliased name.
        return x.go() + self.work()
