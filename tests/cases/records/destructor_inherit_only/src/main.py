# Tests that a child class without __del__ correctly inherits move safety from parent.
# When only the parent has __del__, the child's implicit move ops delegate to the parent's
# custom move constructor, which sets the parent's drop flag to false.
from tpy import Own

class Base:
    name: str
    def __init__(self, name: str):
        self.name = name
    def __del__(self):
        print("drop", self.name)

class Child(Base):
    tag: str
    def __init__(self, name: str, tag: str):
        super().__init__(name)
        self.tag = tag

def consume(c: Own[Child]) -> None:
    print("consumed", c.name, c.tag)

def main():
    c = Child("x", "t1")
    consume(c)
    print("---")

    consume(Child("y", "t2"))
    print("done")

main()
