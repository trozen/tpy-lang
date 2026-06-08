# Send[Pet] for @dynamic protocols: valid wherever bare Pet is valid; the
# C++ representation is the same Adapter/RefAdapter as bare Pet, and sema
# checks the concrete argument's Send-ness at the erasing conversion
# (pointee-level assertion -- see SEND_SYNC_DESIGN.md OQ5).
from tpy import Int32, Send, Own, dynamic
from typing import Protocol

@dynamic
class Pet(Protocol):
    def speak(self) -> Int32: ...

class Dog:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def speak(self) -> Int32:
        return self.n

def greet(p: Send[Pet]) -> None:
    print("speak:", p.speak())

def greet_bare(p: Pet) -> None:
    print("bare:", p.speak())

def consume(p: Send[Own[Pet]]) -> None:
    print("own:", p.speak())

def main() -> None:
    d = Dog(3)
    greet(d)          # lvalue: RefAdapter
    greet(Dog(7))     # rvalue: owning Adapter
    greet_bare(d)
    consume(Dog(5))   # unique_ptr receiver through the marker
    q: Send[Pet] = d  # marker-typed local declaration
    print("local:", q.speak())

main()
