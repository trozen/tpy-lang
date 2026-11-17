# Under `from __future__ import annotations` only annotations are deferred:
# a base and a default are still read when the class / def runs.
from __future__ import annotations
import ea


class C2(ea.Proto):  # tpyc: warning(/'ea.Proto' is read while 'ea' is still being imported/)
    def f(self) -> int:
        return 4


def pick2(c: ea.Color = ea.Color.RED) -> int:  # tpyc: warning(/'ea.Color' is read while 'ea'.*AttributeError/)
    return c.value


def main() -> None:
    print("future:", C2().f(), pick2())
