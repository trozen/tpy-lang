# Constructing an ArrayList from a dict-items view. The ctor's items parameter
# is a nullable union of protocols, so a conformer NAME binds by address while
# an rvalue view has to be named first -- it lands in a temp spelled with its
# own type, whose address is what the slot takes.
from tpy import Int32
from tplib import ArrayList


def main() -> None:
    d = dict([("one", Int32(1)), ("two", Int32(2))])
    from_dict = ArrayList[tuple[str, Int32], 16](d.items())  # rvalue -> temp
    for pair in from_dict:
        print(pair)
    src: list[Int32] = [10, 20, 30]
    from_name = ArrayList[Int32, 8](src)  # a NAME binds without a temp
    print(len(from_dict), len(from_name), from_name[2])


main()
