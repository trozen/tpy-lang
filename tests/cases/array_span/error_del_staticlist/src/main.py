# del on StaticList elements is not supported (no __delitem__)
from tpy import Int32, StaticList

def main() -> None:
    sl = StaticList[Int32, 5]()
    sl.append(Int32(1))
    del sl[0]  # tpyc: error(/not supported/)

main()
