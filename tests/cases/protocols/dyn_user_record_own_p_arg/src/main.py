# User-record method taking Own[P] for @dynamic protocol P: call site
# must wrap concrete arg into unique_ptr<P>. The function-call path
# already wrapped here; this pins the user-record method-dispatch path.
from typing import Protocol
from tpy import dynamic, Own


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Parrot(Pet):
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return self.label


class Shelter:
    count: int

    def __init__(self) -> None:
        self.count = 0

    def admit(self, pet: Own[Pet]) -> None:
        self.count += 1


def main() -> None:
    s = Shelter()
    s.admit(Parrot(label="Polly"))
    s.admit(Parrot(label="Mimi"))
    print(s.count)


main()
