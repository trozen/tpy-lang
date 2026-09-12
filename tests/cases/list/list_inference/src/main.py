from tpy import int32, Span

# Global with annotation -> vector (ListType)
global_list: list[int32] = [1, 2, 3]

# Global without annotation -> also vector (ListType)
global_inferred = [10, 20, 30]

# Local no mutation -> array (ArrayType)
def test_no_mutation() -> int32:
    local = [1, 2, 3]
    return local[0]

# Local with mutation -> vector (ListType)
def test_mutation() -> int32:
    items = [1, 2, 3]
    items.append(4)
    return len(items)

# Passed to list param -> vector (ListType)
def takes_list(x: list[int32]) -> None:
    x.append(42)

def test_list_param() -> None:
    data = [1, 2, 3]
    takes_list(data)

# Passed to Span param -> array (ArrayType)
def takes_span(x: Span[int32]) -> int32:
    return x[0]

def test_span_param() -> int32:
    data = [10, 20, 30]
    return takes_span(data)

# Test global list with annotation
print(global_list[0])  # 1

# Test global list without annotation
print(global_inferred[1])  # 20

# Test no mutation (uses array)
print(test_no_mutation())  # 1

# Test mutation (uses vector)
print(test_mutation())  # 4

# Test list param
test_list_param()

# Test span param
print(test_span_param())  # 10
