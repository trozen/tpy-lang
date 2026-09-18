# A return-only exception never crosses to Python (no @error_return function
# can be exported, and it is a plain value rather than a thrown exception), so
# @export on one is refused with that reason, not the "exposed automatically"
# text a thrown exception class gets.
# tpy: ext_module
from tpy import ReturnException
from tpy.extern import export


@export
class Missing(Exception, ReturnException):  # tpyc: error(/return-only exception.*never crosses to Python/)
    pass
