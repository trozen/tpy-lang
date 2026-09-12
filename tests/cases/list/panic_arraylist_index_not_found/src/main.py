# Test that ArrayList.index() panics when value is not found
from tpy import int32
from tplib import ArrayList

def main() -> None:
    a = ArrayList[int32, 4]()
    a.append(10)
    a.append(20)
    a.index(99)

main()
