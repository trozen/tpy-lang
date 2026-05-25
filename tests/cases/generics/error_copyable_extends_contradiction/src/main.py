# Regression: a record explicitly declaring `extends Copyable` while having
# a deleted copy ctor (here via `__del__`) must NOT be admitted as Copyable.
# Earlier shape of this branch let `_check_record_extends` accept the
# declaration after the predicate denied, defeating the marker and producing
# a cryptic C++ template error at the first Box.clone() call site. The fix
# makes the Copyable marker arm authoritative: predicate denial returns None
# without consulting the extends list, so the existing registration-time
# validator catches the contradiction at the class declaration.
from tpy import Int32, Copyable


class Bad(Copyable):  # tpyc: error(/Class 'Bad' declares implementation of protocol 'Copyable'/)
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

    def __del__(self) -> None:
        pass
