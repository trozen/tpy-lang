# Test that # tpy: namespace in __init__.py propagates to child modules
# tpy: cpp_namespace("myapp")
from tpy import Int32
from mypkg.utils import add

def main() -> None:
    print(add(Int32(10), Int32(32)))

main()
