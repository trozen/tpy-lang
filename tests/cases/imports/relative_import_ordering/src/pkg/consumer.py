print("before first import")
from . import first
print("after first, before second")
from . import second
print("after second")
