# ArrayList.append_default() appends a default-constructed element
from tpy import Int32
from tplib.array_list import ArrayList

def main() -> None:
    a = ArrayList[Int32, 4]()
    a.append(10)
    a.append_default()
    a.append(30)
    print(len(a))
    print(a[0])
    print(a[1])
    print(a[2])

main()
