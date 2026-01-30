from tpy import Int32, StaticList

# Test that out-of-bounds access on StaticList panics at runtime

def test_out_of_bounds() -> None:
    items: StaticList[Int32, 5] = StaticList[Int32, 5]()
    items.append(10)
    items.append(20)
    items.append(30)

    # Access index 10 which is out of bounds (only 3 elements)
    x: Int32 = items[10]
    print(x)

test_out_of_bounds()
