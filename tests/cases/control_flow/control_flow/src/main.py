from tpy import int32

def classify(x: int32) -> int32:
    # Test elif
    if x < 0:
        return -1
    elif x == 0:
        return 0
    else:
        return 1

def check_range(x: int32) -> int32:
    # Test and/or
    if x >= 0 and x <= 10:
        return 1
    return 0

def check_bounds(x: int32) -> int32:
    # Test or
    if x < 0 or x > 100:
        return 1
    return 0

def complex_condition(a: int32, b: int32) -> int32:
    # Test combined and/or with elif
    if a > 0 and b > 0:
        return 1
    elif a < 0 or b < 0:
        return -1
    else:
        return 0

def nested_else_if(x: int32, y: int32) -> int32:
    # Test that genuine else: if stays nested (not flattened like elif)
    if x > 0:
        return 1
    else:
        if y > 0:
            return 2
        else:
            return 3

print(classify(-5))
print(classify(0))
print(classify(5))
print(check_range(5))
print(check_range(15))
print(check_bounds(-1))
print(check_bounds(50))
print(check_bounds(101))
print(complex_condition(1, 1))
print(complex_condition(-1, 1))
print(complex_condition(0, 0))
print(nested_else_if(1, 0))
print(nested_else_if(-1, 1))
print(nested_else_if(-1, -1))
