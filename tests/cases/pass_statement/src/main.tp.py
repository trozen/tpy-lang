from tpy import Int32

# Test 'pass' statement in various contexts

def empty_function() -> None:
    pass

def function_with_pass_branch(x: Int32) -> Int32:
    if x > 0:
        pass
    else:
        return -1
    return x * 2

def pass_in_loop() -> Int32:
    total: Int32 = 0
    i: Int32 = 0
    while i < 10:
        if i % 2 == 0:
            pass
        else:
            total += i
        i += 1
    return total

def pass_in_elif(x: Int32) -> Int32:
    if x < 0:
        return -1
    elif x == 0:
        pass
    else:
        return 1
    return 0

class Counter:
    value: Int32

    def __init__(self, v: Int32) -> None:
        self.value = v

    def do_nothing(self) -> None:
        pass

    def maybe_increment(self, flag: Int32) -> None:
        if flag > 0:
            self.value += 1
        else:
            pass

def test_class_with_pass() -> None:
    obj: Counter = Counter(10)
    obj.do_nothing()
    print(obj.value)
    obj.maybe_increment(1)
    print(obj.value)
    obj.maybe_increment(0)
    print(obj.value)

# Test empty function
empty_function()
print("empty_function called")

# Test pass in branch
print(function_with_pass_branch(5))
print(function_with_pass_branch(-3))

# Test pass in loop
print(pass_in_loop())

# Test pass in elif
print(pass_in_elif(-1))
print(pass_in_elif(0))
print(pass_in_elif(1))

# Test class with pass method
test_class_with_pass()
