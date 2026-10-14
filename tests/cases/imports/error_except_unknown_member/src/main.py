# A dotted except clause naming a member its module lacks is an error, never
# a catch of the local class sharing the last name.
import mx


class Err(Exception):
    pass


def main() -> None:
    try:
        raise Err()
    except mx.Err:  # tpyc: error(/module 'mx' has no attribute 'Err'/)
        print("caught")


main()
