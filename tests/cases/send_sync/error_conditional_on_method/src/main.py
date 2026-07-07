# The conditional if_params_* form is generic-class-only; on a method it is
# rejected. The message is currently the generic decorator-arg one (the all-bool
# stub signature mis-classifies if_params_send as positional -- see the
# _schema_from_stub TODO); a dedicated "generic-classes-only" message would read
# better but the rejection itself is correct.
from tpy import Int32, unsafe_send
from typing import Iterator


class A:
    @unsafe_send(if_params_send=True)  # tpyc: error(/unexpected keyword argument 'if_params_send'/)
    def counts(self, n: Int32) -> Iterator[Int32]:
        i = 0
        while i < n:
            yield i
            i += 1
