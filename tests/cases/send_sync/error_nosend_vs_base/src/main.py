# @nosend contradicts a Send opt-in base class on the same record.
from tpy import Send, nosend

@nosend
class B(Send):  # tpyc: error(/@nosend contradicts the 'Send' base class/)
    pass
