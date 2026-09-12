# Verify that obj.field += val keeps obj alive in liveness analysis,
# preventing premature auto-move of obj.
from tpy import int32, Own

class Counter:
    value: int32

    def __init__(self, v: int32) -> None:
        self.value = v

def take(c: Own[Counter]) -> None:
    print(c.value)

def main() -> None:
    c = Counter(10)
    c.value += 5
    take(c)

main()
