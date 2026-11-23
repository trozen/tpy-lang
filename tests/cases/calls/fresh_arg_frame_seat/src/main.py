# A temporary a generator or coroutine keeps lives as long as the value that
# keeps it: its statement, or the frame when it outlives the statement.
from typing import Iterator
from tpy import int32
import part_frame_seat
import part_frame_seat_tasks
import part_frame_seat_lifted
import part_frame_seat_reads
import part_frame_seat_callees
import part_frame_seat_targets
import part_frame_seat_statements
import part_frame_seat_copies
import part_frame_seat_leaves
import part_frame_seat_owned_params


class Src:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n, n + 1]


def gen(s: Src) -> Iterator[int32]:
    for x in s.xs:
        yield x


def peek(it: Iterator[int32]) -> int32:
    for x in it:
        return x
    return -1


def walrus() -> Iterator[int32]:
    # a walrus binds the generator: it escapes the statement, on the frame
    (g := gen(Src(5)))  # tpyc: ok
    yield 0
    junk = [[k] * 50 for k in range(20)]
    for x in g:
        yield x + len(junk) * 0


def walrus_arg() -> Iterator[int32]:
    # ... inside an argument that only borrows it for the call
    first = peek(g := gen(Src(5)))  # tpyc: ok
    yield first
    junk = [[k] * 50 for k in range(20)]
    for x in g:
        yield x + len(junk) * 0


def main() -> None:
    w = list(walrus())
    print("walrus", w)
    wa = list(walrus_arg())
    print("walrus arg", wa)
    part_frame_seat.main()
    part_frame_seat_tasks.main()
    part_frame_seat_lifted.main()
    part_frame_seat_reads.main()
    part_frame_seat_callees.main()
    part_frame_seat_targets.main()
    part_frame_seat_statements.main()
    part_frame_seat_copies.main()
    part_frame_seat_leaves.main()
    part_frame_seat_owned_params.main()


main()
