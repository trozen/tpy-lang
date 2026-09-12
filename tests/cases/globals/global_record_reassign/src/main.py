# Test reassigning non-value-type global variables with rvalue constructors
from tpy import int32

class Container:
    value: int32
    def __init__(self, value: int32):
        self.value = value

# First assignment (creates global slot)
c = Container(1)
print(c.value)

# Rvalue reassignment (was generating invalid &* on plain T slot)
c = Container(2)
print(c.value)

# Another reassignment
c = Container(3)
print(c.value)
