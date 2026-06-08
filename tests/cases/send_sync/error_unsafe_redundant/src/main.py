# @unsafe_send is redundant with a Send opt-in base class on the same record.
from tpy import Send, unsafe_send

@unsafe_send
class C(Send):  # tpyc: error(/@unsafe_send is redundant with the 'Send' base class/)
    pass
