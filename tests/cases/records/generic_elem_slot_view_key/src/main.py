# A generic record's element-typed slot (`remove(self, value: T)`), library and
# user-defined, at the scalar, str and bytes instantiations: the C++ parameter is
# spelled off the instantiation's type (`param_val_or_ref_t<T>`, `const T&` on a
# readonly method), so at str/bytes it wants the OWNING buffer while the caller's
# key is a view -- the call materializes the owned copy and the lookup finds it.
# Each generic section is followed by its monomorphic twin printing the same line.
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

    def has(self, value: T) -> bool:
        # A readonly method spells the slot `const T&` -- the SECOND generic
        # parameter spelling, distinct from `param_val_or_ref_t<T>`.
        for it in self.items:
            if it == value:
                return True
        return False

    def tag_len(self, tag: str) -> Int32:
        # INVERSE: a non-element str param -- still a std::string_view.
        return len(tag)


class StrLabels:
    # The monomorphic twin of Labels[str, 4]: every slot below is spelled str.
    items: ArrayList[str, 4]

    def __init__(self) -> None:
        self.items = ArrayList[str, 4]()

    def add(self, value: str) -> None:
        self.items.append(value)

    def drop(self, value: str) -> None:
        self.items.remove(value)

    def has(self, value: str) -> bool:
        for it in self.items:
            if it == value:
                return True
        return False


def drop_from(al: ArrayList[Int32, 4], k: Int32) -> None:
    al.remove(k)  # tpyc: ok -- the library generic record's T slot


def drop_literal(al: ArrayList[Int32, 4]) -> None:
    al.remove(3)  # tpyc: ok


def drop_from_record(box: Labels[Int32, 4], k: Int32) -> None:
    box.drop(k)  # tpyc: ok -- a user generic record's T slot


def drop_str_param(al: ArrayList[str, 4], k: str) -> None:
    # A str PARAM (a std::string_view) at the library record's T slot.
    al.remove(k)  # tpyc: ok


def drop_str_local(al: ArrayList[str, 4]) -> None:
    # A str LOCAL bound from a literal is a view too.
    k = "b"
    al.remove(k)  # tpyc: ok


def drop_str_slice(al: ArrayList[str, 4], src: str) -> None:
    # A SLICE result is a view with no owning buffer of its own.
    al.remove(src[0:1])  # tpyc: ok


def drop_bytes(al: ArrayList[bytes, 4], k: bytes) -> None:
    # The bytes twin: the slot wants std::vector<uint8_t>, the key is a span.
    al.remove(k)  # tpyc: ok


def has_str(box: Labels[str, 4], k: str) -> bool:
    # The readonly `const T&` spelling, fed a view.
    return box.has(k)  # tpyc: ok


def has_item[T: Equatable](xs: list[T], v: T) -> bool:
    for x in xs:
        if x == v:
            return True
    return False


def has_item_str(xs: list[str], v: str) -> bool:
    # The monomorphic twin of has_item[str]: `v` is spelled std::string_view.
    for x in xs:
        if x == v:
            return True
    return False


def main() -> None:
    al = ArrayList[Int32, 4]()
    al.append(1)
    al.append(2)
    al.append(3)
    drop_from(al, 2)
    print("scalar", len(al), al[0], al[1])
    drop_literal(al)
    print("scalar", len(al), al[0])

    box = Labels[Int32, 4]()
    box.add(7)
    box.add(8)
    drop_from_record(box, 7)
    print("scalar", len(box.items), box.items[0])
    print("scalar", box.tag_len("abcd"))

    # Library generic record at str, one source form per call.
    sl = ArrayList[str, 4]()
    sl.append("a")
    sl.append("b")
    sl.append("c")
    drop_str_param(sl, "a")
    print("lib_str", len(sl), sl[0])
    drop_str_local(sl)
    print("lib_str", len(sl), sl[0])
    drop_str_slice(sl, "cd")
    print("lib_str", len(sl))

    # ... and the twin, which spells the same slot `std::string_view`.
    tl = ArrayList[str, 4]()
    tl.append("a")
    tl.append("b")
    tl.append("c")
    tl.remove("a")
    print("lib_str", len(tl), tl[0])
    tl.remove("b")
    print("lib_str", len(tl), tl[0])
    tl.remove("c")
    print("lib_str", len(tl))

    # User generic record at str: the mutating slot and the readonly one.
    ul = Labels[str, 4]()
    ul.add("x")
    ul.add("y")
    key = "x"
    print("user_str", has_str(ul, key))
    ul.drop(key)
    print("user_str", len(ul.items), ul.items[0], has_str(ul, key))

    tu = StrLabels()
    tu.add("x")
    tu.add("y")
    print("user_str", tu.has(key))
    tu.drop(key)
    print("user_str", len(tu.items), tu.items[0], tu.has(key))

    # Library generic record at bytes: the slot is a mutable reference there.
    bl = ArrayList[bytes, 4]()
    bl.append(b"a")
    bl.append(b"b")
    bk = b"a"
    drop_bytes(bl, bk)
    print("lib_bytes", len(bl), bl[0])

    # A generic FREE function's T slot, both instantiations plus the twin.
    names = ["p", "q"]
    nk = "p"
    print("free_fn", has_item(names, nk))
    print("free_fn", has_item_str(names, nk))
    keys = [b"p", b"q"]
    bnk = b"p"
    print("free_fn", has_item(keys, bnk))


main()
