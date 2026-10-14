# Test that `__tpy__` "cpp_namespace" in __init__.py propagates to child modules
__tpy__ = {"cpp_namespace": "myapp"}
from tpy import int32
from mypkg.utils import add

def main() -> None:
    print(add(int32(10), int32(32)))

main()
