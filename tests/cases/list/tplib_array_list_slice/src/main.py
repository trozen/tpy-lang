# ArrayList slicing via @overload __getitem__(slice).
from tpy import int32
from tplib import ArrayList

def main() -> None:
    a = ArrayList[int32, 8]()
    for v in [10, 20, 30, 40, 50]:
        a.append(v)

    # Slice with both bounds
    sp = a[1:4]
    for x in sp:
        print(x)

    # Slice with omitted start
    sp2 = a[:2]
    for x in sp2:
        print(x)

    # Slice with omitted stop
    sp3 = a[3:]
    for x in sp3:
        print(x)

    # Full slice
    sp4 = a[:]
    for x in sp4:
        print(x)

    # Negative indices
    sp5 = a[-2:]
    for x in sp5:
        print(x)

    sp6 = a[:-1]
    for x in sp6:
        print(x)

    # Empty result (start >= stop)
    sp7 = a[3:1]
    print(len(sp7))

    # Index access still works
    print(a[0])
    print(a[4])

main()
