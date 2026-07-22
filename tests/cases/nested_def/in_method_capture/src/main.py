# A nested def in a sync method capturing self: direct field mutation and
# transitive mutation via a self method defined AFTER this one (the closure's
# self-call edge resolves in the call-graph fixpoint). Both mutations must be
# visible on the caller's object.
from tpy import Int32


class Acc:
    total: Int32
    count: Int32

    def __init__(self) -> None:
        self.total = 0
        self.count = 0

    def collect(self, k: Int32) -> None:
        bonus = 1

        def feed() -> None:
            self.total += k + bonus
            self._tick()

        feed()
        feed()

    def _tick(self) -> None:
        self.count += 1


def main() -> None:
    a = Acc()
    a.collect(5)
    print(a.total)
    print(a.count)


main()
