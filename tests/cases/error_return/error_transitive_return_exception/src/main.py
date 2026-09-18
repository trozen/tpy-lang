# A return-only exception cannot be subclassed: it is handled by exact type, so
# a subclass adds no dispatch, and one without the marker would be a thrown
# exception over a base that is not one. Each class declares
# `(Exception, ReturnException)` on its own.
from tpy import ReturnException

class BaseError(Exception, ReturnException):
    pass

class SpecificError(BaseError):  # tpyc: error(/cannot subclass 'BaseError'.*handled by exact type/)
    pass
