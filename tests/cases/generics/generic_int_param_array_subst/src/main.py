from tpy import int32, Array


class Buffer[T, N: int]:
    data: Array[T, N]

    def __init__(self) -> None:
        pass

    def get_data(self) -> Array[T, N]:
        return self.data

    def set_data(self, arr: Array[T, N]) -> None:
        self.data = arr


def use_array(arr: Array[int32, 3]) -> None:
    """Function expecting concrete Array[int32, 3]."""
    print(arr[0])
    print(arr[1])
    print(arr[2])


arr_global: Array[int32, 3] = [int32(10), int32(20), int32(30)]

def get_global_array() -> Array[int32, 3]:
    """Function returning concrete Array[int32, 3]."""
    return arr_global


def main() -> None:
    b: Buffer[int32, 3] = Buffer[int32, 3]()

    # Test 1: Pass concrete array to generic method
    concrete: Array[int32, 3] = [int32(1), int32(2), int32(3)]
    b.set_data(concrete)

    # Test 2: Pass generic return type to concrete function
    # get_data() returns Array[T, N], which should substitute to Array[int32, 3]
    use_array(b.get_data())

    # Test 3: Assign concrete return to generic field via method
    b.set_data(get_global_array())
    use_array(b.get_data())


main()
