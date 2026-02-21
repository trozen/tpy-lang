from tpy import Int32

def value_type_uninit() -> None:
    x: Int32
    print(x)  # tpyc: error(/may be used before assignment/)
