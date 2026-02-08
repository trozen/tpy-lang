"""Test importing non-value-type globals from another module."""
from config import Settings, DEFAULT


# Use imported record global — field access, method call
print(DEFAULT.width)
print(DEFAULT.height)
print(DEFAULT.area())
