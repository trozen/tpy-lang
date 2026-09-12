from tpy import int32, Array

def bad_array_literal() -> Array[int32, 3]:
    return [1, 2, 3]  # tpyc: error(/Cannot return local or temporary/)

def main():
    pass

main()
