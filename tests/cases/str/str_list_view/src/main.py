# A list[str] element read binds an owned std::string (a container element never
# lends a view); the mutating sections pin that a later write keeps CPython's value.
from tpy import int32

def test_list_view() -> None:
    """No mutation: still an owned copy."""
    names: list[str] = ["alice", "bob"]
    x = names[int32(0)]  # tpyc: type(str)
    print(x)

def test_list_mutation_fallback() -> None:
    """Source grows after the read: the copy keeps the old value."""
    names: list[str] = ["alice", "bob"]
    x = names[int32(0)]  # tpyc: type(str)
    names.append("carol")
    print(x)

def test_list_reassign_fallback() -> None:
    """Source reassigned after the read: the copy keeps the old value."""
    names: list[str] = ["alice", "bob"]
    x = names[int32(0)]  # tpyc: type(str)
    names = ["dave"]
    print(x)

def test_list_subscript_write_fallback() -> None:
    """Subscript write on the source: the copy keeps the old value."""
    names: list[str] = ["alice", "bob"]
    x = names[int32(0)]  # tpyc: type(str)
    names[int32(0)] = "eve"
    print(x)

def test_list_pop_fallback() -> None:
    """pop() on the source: the copy keeps the old value."""
    names: list[str] = ["alice", "bob"]
    x = names[int32(0)]  # tpyc: type(str)
    names.pop(int32(1))
    print(x)

test_list_view()
test_list_mutation_fallback()
test_list_reassign_fallback()
test_list_subscript_write_fallback()
test_list_pop_fallback()
