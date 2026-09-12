from a import A as Other
from tpy import int32

class Helper:
    def __init__(self) -> None: pass
    def work(self) -> int32:
        return 5
    def caller(self, x: Other) -> int32:
        # Uses the cycle-peer record under its alias and dispatches
        # a method call on it -- exercises both alias resolution and
        # cross-peer method dispatch through the aliased name.
        return x.go() + self.work()
