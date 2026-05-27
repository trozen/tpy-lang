# Generic generator with two type params yielding tuple[K, V], wrapped in a
# try/finally -- exercises a multi-type-param template generator plus the
# borrow-form tuple yield slot and finally on the resumable frame.
from typing import Iterator


def zip_pairs[K, V](ks: list[K], vs: list[V]) -> Iterator[tuple[K, V]]:  # tpyc: ok
    i = 0
    try:
        while i < len(ks) and i < len(vs):
            yield (ks[i], vs[i])
            i += 1
    finally:
        print("zip done")


def main() -> None:
    for k, v in zip_pairs([1, 2], ["a", "b"]):
        print(k, v)


main()
