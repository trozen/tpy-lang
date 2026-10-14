# A dotted except clause naming a member its class lacks is an error that
# names the class, never a catch of a same-named class elsewhere.
import mx


class Missing(Exception):
    pass


def main() -> None:
    try:
        raise Missing()
    except mx.Outer.Missing:  # tpyc: error(/class 'mx.Outer' has no attribute 'Missing'/)
        print("caught")


main()
