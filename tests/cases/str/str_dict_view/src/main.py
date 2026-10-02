# A dict[K, str] value read binds an owned std::string (a container element never
# lends a view); the mutating sections pin that a later write keeps CPython's value.
from tpy import int32

def test_dict_value_view() -> None:
    """No mutation: still an owned copy."""
    d: dict[str, str] = {"hello": "world"}
    v = d["hello"]  # tpyc: type(str)
    print(v)

def test_dict_mutation_fallback() -> None:
    """Dict grows after the read (new key): the copy keeps the old value."""
    d: dict[str, str] = {"hello": "world"}
    v = d["hello"]  # tpyc: type(str)
    d["new"] = "entry"
    print(v)

def test_dict_value_update_fallback() -> None:
    """In-place value update after the read: the copy keeps the old value."""
    d: dict[str, str] = {"hello": "world"}
    v = d["hello"]  # tpyc: type(str)
    d["hello"] = "updated"
    print(v)

def test_dict_int_key_view() -> None:
    """Int key: an owned copy too."""
    d: dict[int32, str] = {int32(1): "one", int32(2): "two"}
    v = d[int32(1)]  # tpyc: type(str)
    print(v)

test_dict_value_view()
test_dict_mutation_fallback()
test_dict_int_key_view()
