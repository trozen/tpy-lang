from tpy import Int32, Bool

def test_bool():
    # Boolean literals
    a: Bool = True
    b: Bool = False

    # Print bools (as 0/1 in if conditions)
    if a:
        print(1)
    else:
        print(0)

    if b:
        print(1)
    else:
        print(0)

    # Boolean in condition
    flag: Bool = True
    count: Int32 = 0
    while flag:
        count = count + 1
        if count == 3:
            flag = False

    print(count)

test_bool()
