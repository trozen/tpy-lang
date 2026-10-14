# Two enums sharing a name across modules are distinct types that do not
# compare; the message tells them apart by module.
from enum import Enum
import mq


class Color(Enum):
    RED = 1


def main() -> None:
    print(Color.RED == mq.Color.RED)  # tpyc: error(/Cannot compare enum types 'Color' and 'mq.Color'/)


main()
