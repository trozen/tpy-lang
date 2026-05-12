# list[Weak[T]] -- storing Weak handles in a container. Mirrors rc_in_list
# for the non-owning side: each Weak observes its target Rc independently;
# upgrade succeeds while the corresponding Rc is alive and returns None
# after the target's last strong reference is dropped.
from tpy import Int32, Own
from tplib.rc import Rc, Weak, make_rc


class Node:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value


def observed_values(observers: list[Weak[Node]]) -> Own[list[Int32]]:
    result: list[Int32] = []
    for w in observers:
        upgraded = w.upgrade()
        if upgraded is None:
            result.append(Int32(-1))
        else:
            result.append(upgraded.get().value)
    return result


def main() -> None:
    a = make_rc(Node(Int32(10)))
    b = make_rc(Node(Int32(20)))
    c = make_rc(Node(Int32(30)))

    observers: list[Weak[Node]] = [a.downgrade(), b.downgrade(), c.downgrade()]

    # All three targets alive: every Weak upgrades.
    for v in observed_values(observers):
        print(v)

    # Mutate via the original Rc, observe through the Weak.
    a.get().value = Int32(99)
    upgraded = observers[0].upgrade()
    assert upgraded is not None
    print(upgraded.get().value)  # 99 -- shared


main()
