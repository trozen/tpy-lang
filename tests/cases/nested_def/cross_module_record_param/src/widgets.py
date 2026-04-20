# Companion module for cross_module_record_param: defines the Widget
# record that main.py's nested def takes as a parameter.
from tpy import Int32


class Widget:
    value: Int32

    def __init__(self, v: Int32) -> None:
        self.value = v

    def get(self) -> Int32:
        return self.value
