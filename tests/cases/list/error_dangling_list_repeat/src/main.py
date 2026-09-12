from tpy import int32, Array

def bad_list_repeat() -> Array[int32, 4]:
    return [0] * 4  # tpyc: error(/Cannot return local or temporary/)

def main():
    pass

main()
