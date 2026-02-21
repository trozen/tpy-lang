# Tests that moving an object with __del__ does not cause double-drop.
# The drop flag (__tpy_owned_) prevents the destructor body from running on moved-from objects.
from tpy import Own

class Tracker:
    name: str
    def __init__(self, name: str):
        self.name = name
    def __del__(self):
        print("drop", self.name)

def consume(t: Own[Tracker]) -> None:
    print("consumed", t.name)

def main():
    # Temporary passed to Own param -- temporary is moved, not copied
    consume(Tracker("a"))
    print("---")

    # Local passed to Own param via auto-move at last use
    t = Tracker("b")
    consume(t)
    print("---")

main()
