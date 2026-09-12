# @override on a @dynamic protocol implementation: clean compile, no warning.
# The "use a @dynamic protocol" suffix must not appear since one is already in use.
from tpy import int32, dynamic
from typing import Protocol, override

@dynamic
class Speaker(Protocol):
    def speak(self) -> str: ...


class Dog(Speaker):
    @override
    def speak(self) -> str:  # tpyc: ok
        return "woof"


def greet(s: Speaker) -> None:
    print(s.speak())


def main() -> None:
    greet(Dog())

main()
