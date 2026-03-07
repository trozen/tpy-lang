from tpy import Int32

def value_type_uninit() -> None:
    x: Int32
    print(x)  # tpyc: error(/may not be assigned at this point/)
