# detach() consumes the handle: the thread runs independently and dropping the
# consumed handle does NOT panic (contrast panic_spawn_unconsumed_drop).
from tpy.thread import spawn


class Work:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def run(self) -> int:
        return self.n * 2


def main() -> None:
    h = spawn[int, Work](Work(21))
    h.detach()
    print("detached")


main()
