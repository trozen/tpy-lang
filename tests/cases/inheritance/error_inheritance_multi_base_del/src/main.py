# BaseN.__del__(self) is rejected outright: __del__ compiles to a C++
# destructor, not a callable member, and C++ invokes each base destructor
# automatically in any case.


class Parent:
    def __del__(self) -> None:
        pass


class Child(Parent):
    def cleanup(self) -> None:
        Parent.__del__(self)  # tpyc: error(/is not callable via the unbound-self form/)
