# Rc[T] drops the shared payload exactly once when the last clone goes
# out of scope. Verified via __del__ side effect.
from tpy import Int32
from tplib import Rc, make_rc


class State:
    label: str

    def __init__(self, label: str) -> None:
        self.label = label
        print("init", label)

    def __del__(self) -> None:
        print("del", self.label)


def use(r: Rc[State]) -> None:
    print("use sees", r.get().label)


def single_owner() -> None:
    r = make_rc(State("solo"))
    use(r)
    # State("solo") destructs when r goes out of scope.


def shared_via_clone() -> None:
    r1 = make_rc(State("shared"))
    r2 = r1.clone()
    use(r1)
    use(r2)
    # both r1 and r2 drop here; State("shared") destructs exactly once.


def main() -> None:
    print("--- single owner ---")
    single_owner()
    print("--- shared via clone ---")
    shared_via_clone()
    print("--- done ---")


main()
