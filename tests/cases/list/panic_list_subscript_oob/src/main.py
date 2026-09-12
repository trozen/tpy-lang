from tpy import int32

# Test that subscript out-of-bounds access on list panics at runtime

def test_list_subscript_oob() -> None:
    nums: list[int32] = [1, 2, 3]

    # Access index 10 via subscript - out of bounds (only 3 elements)
    x: int32 = nums[10]
    print(x)

test_list_subscript_oob()
