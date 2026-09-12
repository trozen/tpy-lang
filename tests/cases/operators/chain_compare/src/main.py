# Chained comparison operators: a < b < c desugars to (a < b) and (b < c)
from tpy import int32

def test_basic() -> None:
    print(1 < 2 < 3)
    print(1 < 2 < 2)
    print(1 < 3 < 2)

def test_variables() -> None:
    x: int32 = 5
    print(int32(0) < x < int32(10))
    print(int32(0) < x < int32(5))

def test_mixed_ops() -> None:
    print(1 <= 2 < 3)
    print(1 < 2 <= 2)

def test_equality() -> None:
    print(1 == 1 == 1)
    print(1 == 1 == 2)
    print(1 != 2 != 1)
    print(1 != 2 != 2)

def test_triple() -> None:
    print(1 < 2 < 3 < 4)
    print(1 < 2 < 3 < 3)

def test_descending() -> None:
    print(3 >= 2 >= 1)
    print(3 >= 2 >= 3)

def test_short_circuit() -> None:
    x: int32 = 10
    y: int32 = 0
    # First comparison false -> second must not matter
    # (verifies && short-circuit from desugaring)
    print(5 < 3 < 100)

def test_float() -> None:
    print(1.0 < 2.5 < 3.0)
    print(1.0 < 2.5 < 2.0)

def test_in_condition() -> None:
    x: int32 = 5
    if 0 < x < 10:
        print("in range")
    else:
        print("out of range")

def test_as_expression() -> None:
    result: bool = 1 < 2 < 3
    print(result)
    print(not (1 < 2 < 3))
    print(1 < 2 < 3 and 4 < 5 < 6)

call_count: int32 = 0

def get_mid() -> int32:
    global call_count
    call_count = call_count + int32(1)
    return int32(5)

def get_high() -> int32:
    global call_count
    call_count = call_count + int32(1)
    return int32(10)

def get_top() -> int32:
    global call_count
    call_count = call_count + int32(1)
    return int32(20)

def test_single_eval() -> None:
    # Function call as intermediate -- must be evaluated exactly once
    global call_count
    call_count = int32(0)
    print(int32(0) < get_mid() < int32(10))
    print(call_count)

def test_short_circuit_operands() -> None:
    # Two function calls: a < f() < g()
    # When first comparison fails, g() must NOT be called
    global call_count
    call_count = int32(0)
    print(int32(99) < get_mid() < get_high())
    # get_mid() called (returns 5), 99 < 5 is false -> get_high() skipped
    print(call_count)
    # When first comparison passes, both are called
    call_count = int32(0)
    print(int32(0) < get_mid() < get_high())
    # get_mid() called (returns 5), 0 < 5 true -> get_high() called (returns 10), 5 < 10 true
    print(call_count)

def test_triple_short_circuit() -> None:
    # 3-pair chain with complex intermediates: a < f() < g() < h()
    # Exercises the inner wrap loop (n >= 3) in chained-compare codegen.
    global call_count
    # All pass: 0 < 5 < 10 < 20 -- all three helpers evaluate.
    call_count = int32(0)
    print(int32(0) < get_mid() < get_high() < get_top())
    print(call_count)
    # Fail at 2nd compare (get_high() < 3 is false): get_top() must skip.
    call_count = int32(0)
    print(int32(0) < get_mid() < get_high() < int32(3))
    print(call_count)
    # Fail at 1st compare (99 < 5 is false): both get_high() and get_top() skip.
    call_count = int32(0)
    print(int32(99) < get_mid() < get_high() < get_top())
    print(call_count)

test_basic()
test_variables()
test_mixed_ops()
test_equality()
test_triple()
test_descending()
test_short_circuit()
test_float()
test_in_condition()
test_as_expression()
test_single_eval()
test_short_circuit_operands()
test_triple_short_circuit()
