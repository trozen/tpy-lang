# Test compile error for Optional[Model] field (not yet supported).
from tpy import Int32, try_parse
from tplib.json import JsonReader, JsonToken, JsonWriter
from tplib.json.model import model

@model
class Inner:
    x: Int32

@model
class Outer:  # tpyc: error(/Optional\[record\] fields are not yet supported/)
    name: str
    inner: Inner | None = None
