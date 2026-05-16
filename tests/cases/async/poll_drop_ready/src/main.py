# Dropping a Ready Poll[T] without calling .value() must run T's
# destructor exactly once (Poll's __del__ takes the slot), and consuming
# via .value() must NOT double-drop on the moved-from Poll.
from tpy import Own, nocopy
from tpy.coro import Poll


@nocopy
class Probe:
    tag: int

    def __init__(self, tag: int) -> None:
        self.tag = tag
        print(f"Probe({tag}) init")

    def __del__(self) -> None:
        print(f"Probe({self.tag}) drop")


def make_ready(tag: int) -> Own[Poll[Probe]]:
    return Poll[Probe].ready(Probe(tag))


def drop_path() -> None:
    p = make_ready(1)
    print("constructed, is_ready:", p.is_ready())
    print("dropping ready poll without consume")


def consume_path() -> None:
    # After q.value() the Own[Self] receiver makes q unreachable -- the
    # borrow checker rejects any further access (no double-take).
    q = make_ready(2)
    v = q.value()
    print("consumed value tag:", v.tag)


def main() -> None:
    drop_path()
    print("---")
    consume_path()


main()
