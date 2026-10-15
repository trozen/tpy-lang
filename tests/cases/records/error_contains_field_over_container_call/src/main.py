# A `__contains__` receiver that is a FIELD read off a container-returning call:
# the method call admits a field over an F1-record-returning call only.
# Concretely, `"k" in boxes()[0].jar`; TPy rejects that `in` test today, as it
# rejects the explicit `boxes()[0].jar.__contains__("k")`.
from tpy import Own


class Jar:
    keys: set[str]

    def __init__(self) -> None:
        self.keys = {"k"}

    def __contains__(self, name: str) -> bool:
        return name in self.keys


class JarBox:
    jar: Jar

    def __init__(self) -> None:
        self.jar = Jar()


def boxes() -> Own[list[JarBox]]:
    return [JarBox()]


def probe() -> None:
    print("k" in boxes()[0].jar)  # tpyc: error(/method.recv.field_chain/)


probe()
