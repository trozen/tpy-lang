# Cross-module import of globals defined via tuple unpacking
from tpy import int32
from config import lo, hi

def show() -> None:
    print(lo)
    print(hi)

print(lo)
print(hi)
show()
