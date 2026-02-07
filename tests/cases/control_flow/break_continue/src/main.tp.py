from tpy import Int32

def test_break():
    # Break in while loop
    i: Int32 = 0
    while i < 10:
        if i == 5:
            break
        i += 1
    print(i)  # 5

def test_continue():
    # Continue in for loop - skip even numbers
    total: Int32 = 0
    for i in range(10):
        if i % 2 == 0:
            continue
        total += i
    print(total)  # 1 + 3 + 5 + 7 + 9 = 25

def test_nested_break():
    # Break only exits innermost loop
    count: Int32 = 0
    for i in range(3):
        for j in range(5):
            if j == 2:
                break
            count += 1
    print(count)  # 2 * 3 = 6 (j goes 0, 1 then breaks, 3 times)

def test_nested_continue():
    # Continue only affects innermost loop
    count: Int32 = 0
    for i in range(3):
        for j in range(4):
            if j == 1:
                continue
            count += 1
    print(count)  # 3 * 3 = 9 (j skips 1, so 0, 2, 3 for each i)

test_break()
test_continue()
test_nested_break()
test_nested_continue()
