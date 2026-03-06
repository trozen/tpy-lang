# Test deferred resolution of list repeat: unmutated -> Array, mutated -> list
from tpy import Int32, Span

def test_array_resolution() -> None:
    # Unmutated repeat with constant count -> Array[Int32, 5]
    x = [0] * 5  # tpyc: type(/Array\[/)
    print(len(x))
    for v in x:
        print(v)

def test_list_promotion() -> None:
    # Mutated repeat -> promoted to list[Int32]
    y = [0] * 3  # tpyc: type(/list\[/)
    y.append(42)
    print(len(y))
    for v in y:
        print(v)

def test_annotated_list() -> None:
    # Explicit list annotation -> list[Int32]
    z: list[Int32] = [1] * 4  # tpyc: type(/list\[/)
    z.append(5)
    print(len(z))
    for v in z:
        print(v)

def test_subscript_stays_array() -> None:
    # Subscript on constant-count repeat -> stays Array (Array supports operator[])
    w = [9] * 3  # tpyc: type(/Array\[/)
    print(w[0])
    print(w[1])
    print(w[2])

def takes_span(s: Span[Int32]) -> None:
    for v in s:
        print(v)

def test_variable_repeat_assigned_to_span() -> None:
    # Variable count repeat assigned to variable, then passed to Span -> list (lvalue)
    n: Int32 = 2
    x = [6] * n
    takes_span(x)

test_array_resolution()
test_list_promotion()
test_annotated_list()
test_subscript_stays_array()
test_variable_repeat_assigned_to_span()
