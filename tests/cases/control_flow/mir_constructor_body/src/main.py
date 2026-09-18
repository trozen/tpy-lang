# Constructor-local tuples retain self by reference but snapshot scalar fields:
# writes through the captured self change the result without changing that snapshot.
from tpy import int32


class Cell:
    value: int32
    before: int32

    def __init__(self, value: int32, reset: bool):
        self.value = value
        self.before = 0
        # Capture the initialized receiver and its current scalar independently.
        saved = (self, self.value)  # tpyc: ok
        if reset:
            # This must mutate the receiver, not a copy held by the tuple.
            saved[0].value = 7  # tpyc: ok
        self.before = saved[1]


def main():
    unchanged = Cell(3, False)
    changed = Cell(3, True)
    print("unchanged", unchanged.value, unchanged.before)
    print("changed", changed.value, changed.before)


main()
