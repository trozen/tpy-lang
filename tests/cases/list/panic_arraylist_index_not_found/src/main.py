# Test that ArrayList.index() panics when value is not found
from tpy import Int32
from tplib import ArrayList

def main() -> None:
    a = ArrayList[Int32, 4]()
    a.append(10)
    a.append(20)
    a.index(99)

main()
