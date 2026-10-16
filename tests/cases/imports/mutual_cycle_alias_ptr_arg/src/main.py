# A field of a recursive alias whose argument is a class of the other module
# of an import cycle builds when the alias holds that argument behind a Ptr.
import a
import b


def main() -> None:
    bag = a.Bag()
    print("count", bag.count())
    print("via peer", b.count_bag(bag))


main()
