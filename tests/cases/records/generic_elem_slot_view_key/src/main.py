# A generic record's element-typed slot (`remove(self, value: T)`), library and
# user-defined, at a scalar instantiation: the lookup key passes bare and the
# lookup finds it.
# The str/bytes instantiations of the same slot do NOT compile and cannot be
# pinned here: `param_val_or_ref_t<T>` renders `const std::string&` where a
# plain `str` param renders `std::string_view`, so the view the caller passes
# has nothing to bind to (BUGS.md#generic-slot-str-bytes-param-form) -- the
# comp phase passes and only the C++ build fails.
# The inverse leg pins that a NON-element `str` param on the same record keeps
# its view form, which is what makes the element slot's spelling the outlier.
from tpy import Equatable, Int32
from tplib import ArrayList


class Labels[T: Equatable, N: int]:
    items: ArrayList[T, N]

    def __init__(self) -> None:
        self.items = ArrayList[T, N]()

    def add(self, value: T) -> None:
        self.items.append(value)  # tpyc: warning(/may copy T into owned storage/)

    def drop(self, value: T) -> None:
        # The element slot: `value` is the record's own T.
        self.items.remove(value)

    def tag_len(self, tag: str) -> Int32:
        # INVERSE: a non-element str param -- still a std::string_view.
        return len(tag)


def drop_from(al: ArrayList[Int32, 4], k: Int32) -> None:
    al.remove(k)  # tpyc: ok -- the library generic record's T slot


def drop_literal(al: ArrayList[Int32, 4]) -> None:
    al.remove(3)  # tpyc: ok


def drop_from_record(box: Labels[Int32, 4], k: Int32) -> None:
    box.drop(k)  # tpyc: ok -- a user generic record's T slot


def main() -> None:
    al = ArrayList[Int32, 4]()
    al.append(1)
    al.append(2)
    al.append(3)
    drop_from(al, 2)
    print(len(al), al[0], al[1])
    drop_literal(al)
    print(len(al), al[0])

    box = Labels[Int32, 4]()
    box.add(7)
    box.add(8)
    drop_from_record(box, 7)
    print(len(box.items), box.items[0])
    print(box.tag_len("abcd"))


main()
