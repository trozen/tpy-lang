# Companion module for cross_module_record_param: defines the Widget
# record that main.py's nested def takes as a parameter.
from tpy import int32


class Widget:
    value: int32

    def __init__(self, v: int32) -> None:
        self.value = v

    def get(self) -> int32:
        return self.value
