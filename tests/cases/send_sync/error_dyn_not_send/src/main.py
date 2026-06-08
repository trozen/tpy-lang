# A non-Send conformer (StrView field) is rejected at the conversion that
# erases it into a Send[Pet] adapter.
from tpy import Int32, Send, StrView, dynamic
from typing import Protocol

@dynamic
class Pet(Protocol):
    def speak(self) -> Int32: ...

class ViewPet:
    s: StrView

    def __init__(self, s: StrView) -> None:
        self.s = s

    def speak(self) -> Int32:
        return len(self.s)

def greet(p: Send[Pet]) -> None:
    print("speak:", p.speak())

def main() -> None:
    v = ViewPet("abc")
    greet(v)  # tpyc: error(/'ViewPet' is not Send -- cannot use it where 'Send\[Pet\]' is expected/)

main()
