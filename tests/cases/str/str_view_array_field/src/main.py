# String view deduction for Array subscript and record field sources
from tpy import int32, Array, readonly

class Person:
    name: str
    age: int32
    def __init__(self, name: str, age: int32) -> None:
        self.name = name
        self.age = age

    def rename(self, new_name: str) -> None:
        self.name = new_name

    @readonly
    def greeting(self) -> str:
        return "Hi, " + self.name

def test_array_subscript_view() -> None:
    """Array element access -> string_view (stable storage)."""
    arr: Array[str, 3] = ["hello", "world", "test"]
    s = arr[int32(0)]  # tpyc: type(StrView)
    print(s)

def test_array_subscript_mutated() -> None:
    """Array element overwrite invalidates view -> falls back to str."""
    arr: Array[str, 2] = ["old", "value"]
    s = arr[int32(0)]  # tpyc: type(str)
    arr[int32(0)] = "new"
    print(s)

def test_record_field_view() -> None:
    """Record field access -> string_view (stable storage)."""
    p = Person("Alice", int32(30))
    s = p.name  # tpyc: type(StrView)
    print(s)

def test_record_field_mutated() -> None:
    """Record field reassignment invalidates view -> falls back to str."""
    p = Person("Bob", int32(25))
    s = p.name  # tpyc: type(str)
    p.name = "Charlie"
    print(s)

def test_record_reassigned() -> None:
    """Record variable reassignment invalidates view -> falls back to str."""
    p = Person("Dave", int32(40))
    s = p.name  # tpyc: type(str)
    p = Person("Eve", int32(35))
    print(s)

def test_record_method_mutates() -> None:
    """Non-readonly method call on source record invalidates view."""
    p = Person("Frank", int32(50))
    s = p.name  # tpyc: type(str)
    p.rename("Grace")
    print(s)

def test_readonly_method_preserves_view() -> None:
    """Readonly method call does NOT invalidate the view."""
    p = Person("Helen", int32(60))
    s = p.name  # tpyc: type(StrView)
    g = p.greeting()
    print(s)

def test_field_aug_assign_mutates() -> None:
    """Field aug-assign invalidates view -> falls back to str."""
    p = Person("Iris", int32(70))
    s = p.name  # tpyc: type(str)
    p.name += "!"
    print(s)

def test_array_passed_to_func(arr: Array[str, 2]) -> None:
    """Array passed to non-readonly func -> falls back to str."""
    s = arr[int32(0)]  # tpyc: type(str)
    mutate_array(arr)
    print(s)

def mutate_array(arr: Array[str, 2]) -> None:
    arr[int32(0)] = "mutated"

def test_multiple_views_one_source() -> None:
    """Multiple views from same source; one mutation invalidates all."""
    p = Person("X", int32(1))
    a = p.name  # tpyc: type(str)
    b = p.name  # tpyc: type(str)
    p.name = "Y"
    print(a)
    print(b)

test_array_subscript_view()
test_array_subscript_mutated()
test_record_field_view()
test_record_field_mutated()
test_record_reassigned()
test_record_method_mutates()
test_readonly_method_preserves_view()
test_field_aug_assign_mutates()
test_array_passed_to_func(["first", "second"])
test_multiple_views_one_source()
