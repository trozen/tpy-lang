# Phase 2.1 leaf migration: Slot TPy class. Verifies the slot entry
# constructs, is_done() reflects the empty-box state, and the
# generation / runnable fields are mutable. No executor yet -- the
# class is a leaf data type that Phase 2.2 will compose into the
# slot table list.
from asyncio._executor import Slot


def main() -> None:
    s = Slot()
    print("done?", s.is_done())
    print("runnable?", s.runnable)
    print("gen0:", s.generation)

    s.runnable = True
    s.generation = 3
    print("runnable after set?", s.runnable)
    print("gen after set:", s.generation)

    # Box stays empty (no factory yet), so is_done remains true.
    print("done after set?", s.is_done())


main()
