from tpy import int32

def test_bool():
    # Boolean literals with explicit type
    a: bool = True
    b: bool = False

    # Type inference for bool
    inferred_true = True
    inferred_false = False

    # Print bools (as 0/1 in if conditions)
    if a:
        print(1)
    else:
        print(0)

    if b:
        print(1)
    else:
        print(0)

    # Inferred bools work the same
    if inferred_true:
        print(1)
    if inferred_false:
        print(0)

    # Boolean in while condition (inferred type)
    flag = True
    count: int32 = 0
    while flag:
        count = count + 1
        if count == 3:
            flag = False

    print(count)

test_bool()
