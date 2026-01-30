from tpy import Int32, StaticList

def bad_staticlist_ctor() -> StaticList[Int32, 4]:
    return StaticList[Int32, 4]()  # tpyc: error(/Cannot return local or temporary/)

def main():
    pass

main()
