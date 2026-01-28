from tpy import Int32, Array

def bad_list_repeat() -> Array[Int32, 4]:
    return [0] * 4  # tpyc: error(/Cannot return local or temporary/)

def main():
    pass

main()
