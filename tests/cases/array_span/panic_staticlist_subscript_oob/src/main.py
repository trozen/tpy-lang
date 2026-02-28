from tpy import Int32, StaticList

# Test that subscript out-of-bounds access on StaticList panics at runtime

def test_subscript_oob() -> None:
    items: StaticList[Int32, 5] = StaticList[Int32, 5]()
    items.append(10)
    items.append(20)
    items.append(30)

    # Access index 10 via subscript - out of bounds (only 3 elements)
    x: Int32 = items[10]
    print(x)

test_subscript_oob()
