# Weak[T] as a record field. Locks down the storage form for Weak handles
# inside records (mirrors rc_in_field for the Rc side).
from __future__ import annotations
from tpy import int32, Own
from tplib.rc import Rc, Weak


class Observer:
    target: Weak[Counter]

    def __init__(self, target: Own[Weak[Counter]]) -> None:
        self.target = target

    def read(self) -> int32:
        upgraded = self.target.upgrade()
        if upgraded is None:
            return int32(-1)
        return upgraded.get().value


class Counter:
    value: int32

    def __init__(self, v: int32) -> None:
        self.value = v


def make_observer_with_dead_target() -> Own[Observer]:
    # Local rc dies at function return; obs.target outlives it as a Weak.
    rc = Rc.new(Counter(int32(99)))
    return Observer(rc.downgrade())


def main() -> None:
    c = Rc.new(Counter(int32(10)))
    obs = Observer(c.downgrade())

    print(obs.read())  # 10

    c.get().value = int32(42)
    print(obs.read())  # 42

    # Dead-payload path: target's Rc is gone, upgrade() returns None,
    # Observer.read() returns the sentinel -1.
    dead_obs = make_observer_with_dead_target()
    print(dead_obs.read())  # -1


main()
