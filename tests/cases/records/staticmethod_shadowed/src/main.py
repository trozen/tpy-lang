"""Test that local variables correctly shadow class names for method calls."""
from tpy import Int32

class Helper:
    value: Int32

    def __init__(self, v: Int32):
        self.value = v

    @staticmethod
    def add(a: Int32, b: Int32) -> Int32:
        return a + b

    def get(self) -> Int32:
        return self.value

def use_helper(Helper: Helper) -> Int32:
    # Inside this function, 'Helper' is the parameter (an instance), not the class
    # So Helper.get() should call the instance method, not try to find a static method
    return Helper.get()

# Static method call via class name
print(Helper.add(10, 20))

# Create instance and call function
h = Helper(42)
print(use_helper(h))
