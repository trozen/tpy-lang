# String subscript on list[str] infers string_view; falls back to std::string
# if the list is mutated (append, insert, subscript write, etc.) after the access.
from tpy import Int32

def test_list_view() -> None:
    """No mutation: string_view."""
    names: list[str] = ["alice", "bob"]
    x = names[Int32(0)]  # tpyc: type(StrView)
    print(x)

def test_list_mutation_fallback() -> None:
    """Source mutated after borrow: falls back to std::string."""
    names: list[str] = ["alice", "bob"]
    x = names[Int32(0)]  # tpyc: type(str)
    names.append("carol")
    print(x)

def test_list_reassign_fallback() -> None:
    """Source reassigned after borrow: falls back to std::string."""
    names: list[str] = ["alice", "bob"]
    x = names[Int32(0)]  # tpyc: type(str)
    names = ["dave"]
    print(x)

def test_list_subscript_write_fallback() -> None:
    """Subscript write on source: falls back to std::string."""
    names: list[str] = ["alice", "bob"]
    x = names[Int32(0)]  # tpyc: type(str)
    names[Int32(0)] = "eve"
    print(x)

def test_list_pop_fallback() -> None:
    """pop() on source: falls back to std::string."""
    names: list[str] = ["alice", "bob"]
    x = names[Int32(0)]  # tpyc: type(str)
    names.pop(Int32(1))
    print(x)

test_list_view()
test_list_mutation_fallback()
test_list_reassign_fallback()
test_list_subscript_write_fallback()
test_list_pop_fallback()
