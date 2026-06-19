# os.environ iteration: __iter__/keys/values/items/__len__. Filters to vars
# this program sets (distinctive prefix) and sorts, so output is independent of
# the ambient environment. len is checked relative to a baseline. Byte-compared
# against CPython.
import os

_PREFIX = "TPY_ITER_"


def main():
    base_len = len(os.environ)
    os.environ["TPY_ITER_A"] = "va"
    os.environ["TPY_ITER_B"] = "vb"
    os.environ["TPY_ITER_C"] = "vc"
    print(len(os.environ) == base_len + 3)

    # __iter__ yields keys.
    iter_keys: list[str] = []
    for k in os.environ:
        if k.startswith(_PREFIX):
            iter_keys.append(k)
    iter_keys.sort()
    print(iter_keys)

    # keys() matches __iter__.
    key_list: list[str] = []
    for k in os.environ.keys():
        if k.startswith(_PREFIX):
            key_list.append(k)
    key_list.sort()
    print(key_list == iter_keys)

    # items() pairs, filtered and sorted by key.
    pairs: list[str] = []
    for k, v in os.environ.items():
        if k.startswith(_PREFIX):
            pairs.append(k + "=" + v)
    pairs.sort()
    print(pairs)

    # values() for the keys we set, gathered via items() to pick the right ones.
    vals: list[str] = []
    for k, v in os.environ.items():
        if k.startswith(_PREFIX):
            vals.append(v)
    vals.sort()
    # values() returns the same multiset.
    vlist: list[str] = []
    for x in os.environ.values():
        vlist.append(x)
    matched: list[str] = []
    for v in vals:
        if v in vlist:
            matched.append(v)
    print(matched)


main()
