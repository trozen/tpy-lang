# A dict view borrows its dict, so returning the view of a LOCAL dict hands
# back a view of storage that dies at the return -- rejected like StrView.
from tpy import int32


def keys_of_local() -> dict_keys[str, int32]:
    d = {"a": 1, "b": 2}
    return d.keys()  # tpyc: error(/Cannot return dict_keys referencing a local or temporary/)


def main() -> None:
    print(len(keys_of_local()))


main()
