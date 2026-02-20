# Tests that reassigning a variable with __del__ properly destroys intermediate values.
# The move-assignment operator uses destroy-and-reconstruct to ensure cleanup runs.
# Covers straight-line, loop, and conditional reassignment patterns.
from tpy import Int32

class Resource:
    name: str
    def __init__(self, name: str):
        self.name = name
    def __del__(self):
        print("drop", self.name)

def test_straight():
    r = Resource("a")
    r = Resource("b")
    r = Resource("c")
    print("alive:", r.name)

def test_loop():
    r = Resource("init")
    for i in range(3):
        r = Resource("loop")
    print("alive:", r.name)

def test_conditional(flag: Int32):
    r = Resource("start")
    if flag > 0:
        r = Resource("branch")
    print("alive:", r.name)

class Base:
    tag: str
    def __init__(self, tag: str):
        self.tag = tag
    def __del__(self):
        print("~Base", self.tag)

class Child(Base):
    def __init__(self, tag: str):
        super().__init__(tag)
    def __del__(self):
        print("~Child", self.tag)

def test_inherit():
    c = Child("x")
    c = Child("y")
    c = Child("z")
    print("alive:", c.tag)

test_straight()
print("---")
test_loop()
print("---")
test_conditional(1)
print("---")
test_conditional(0)
print("---")
test_inherit()
