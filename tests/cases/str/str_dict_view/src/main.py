# String subscript on dict[K, str] infers string_view; falls back to std::string
# if the dict is mutated after the access.
from tpy import int32

def test_dict_value_view() -> None:
    """No mutation: string_view for dict value."""
    d: dict[str, str] = {"hello": "world"}
    v = d["hello"]  # tpyc: type(StrView)
    print(v)

def test_dict_mutation_fallback() -> None:
    """Dict mutated after borrow (new key): falls back to std::string."""
    d: dict[str, str] = {"hello": "world"}
    v = d["hello"]  # tpyc: type(str)
    d["new"] = "entry"
    print(v)

def test_dict_value_update_fallback() -> None:
    """In-place value update invalidates the view: falls back to std::string."""
    d: dict[str, str] = {"hello": "world"}
    v = d["hello"]  # tpyc: type(str)
    d["hello"] = "updated"
    print(v)

def test_dict_int_key_view() -> None:
    """Int key: string_view for dict value."""
    d: dict[int32, str] = {int32(1): "one", int32(2): "two"}
    v = d[int32(1)]  # tpyc: type(StrView)
    print(v)

test_dict_value_view()
test_dict_mutation_fallback()
test_dict_int_key_view()
