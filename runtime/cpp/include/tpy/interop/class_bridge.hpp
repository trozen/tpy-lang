#pragma once
// Runtime support for exposing a TPy class (record) as a CPython heap type.
//
// An exposed instance embeds the TPy C++ payload immediately after the
// PyObject header (Instance<T>); the PyObject owns the payload, so multiple
// Python references to one instance alias it and see mutations (correct Python
// reference semantics -- unlike the by-copy container boundary). The generated
// glue builds the type via PyType_FromSpec, wiring tp_init (placement-new the
// payload from marshalled __init__ args), tp_dealloc (instance_dealloc<T>), and
// per-method/per-field wrappers. The class-typed marshalling here is named (not
// to_py/from_py overloads) because T is a user type unknown to marshal.hpp: the
// glue calls these with the module-static PyTypeObject* for the class.
//
// IN  (param): instance_payload<T> type-checks and returns a borrow of the live
//              embedded payload -- mutations through it write through to the
//              same PyObject (param aliasing holds).
// OUT (return): instance_to_py<T> allocates a NEW instance and moves the value
//              into its payload -- identity is NOT preserved across a return
//              (the declared in-aliases / out-copies asymmetry).

#include <cstddef>  // std::max_align_t (the hierarchy-uniform payload offset)
#include <memory>   // std::destroy_at (the generated tp_init re-init path)
#include <new>
#include <type_traits>
#include <utility>

#include "tpy/interop/cpython_h.hpp"
#include "tpy/interop/marshal.hpp"  // MarshalError + the scalar marshallers

namespace tpy::interop {

// The instance layout: PyObject header, a constructed-payload flag, then the
// embedded TPy payload. tp_basicsize == sizeof(Instance<T>); the payload lives
// at a fixed offset the generated slots reinterpret_cast to.
//
// `initialized` distinguishes a constructed payload from the zero-filled
// storage PyType_GenericAlloc hands out: an instance can exist with an
// unconstructed payload (tp_new/GenericAlloc ran but tp_init did not -- e.g.
// `Cls.__new__(Cls)`), and tp_init can run more than once (an explicit
// `obj.__init__(...)`). Both the destructor and re-init must consult it so they
// never destroy a payload that was never constructed, nor leak one being
// overwritten.
//
// Inheritance contract: a base-emitted wrapper may receive a derived instance
// and read it through Instance<Base>. That is sound because (a) the payload
// member is pinned to a T-independent offset (the alignas below, guarded by
// the alignment assert), and (b) the Base subobject sits at offset 0 of a
// derived payload -- guaranteed by the Itanium C++ ABI for a single
// non-virtual base (GCC/Clang are already the compiler floor; polymorphic
// payloads, whose vptr would displace the base, are rejected below and the
// boundary validator rejects multiple exposed bases). A derived payload is
// not standard-layout, so the casts here lean on that ABI layout rather than
// the standard's blessing.
template <class T>
struct Instance {
    static_assert(!std::is_polymorphic_v<T>,
                  "an exposed class payload must not be polymorphic -- a vptr "
                  "would displace the base subobject the boundary casts rely on");
    static_assert(alignof(T) <= alignof(std::max_align_t),
                  "an over-aligned exposed class payload would break the "
                  "hierarchy-uniform payload offset");
    cpy::PyObject ob_base;
    bool initialized;
    alignas(std::max_align_t) T payload;
};

// tp_dealloc: run the C++ destructor (so Own/Box/Rc/container fields release),
// then free the block via the type's tp_free slot. Wired as the Py_tp_dealloc
// slot for every exposed class. Skips the destructor when the payload was never
// constructed (allocated-but-not-tp_init'd), which would otherwise run ~T over
// zero-filled storage.
template <class T>
void instance_dealloc(cpy::PyObject *self) {
    auto *inst = reinterpret_cast<Instance<T> *>(self);
    if (inst->initialized) {
        inst->payload.~T();
    }
    using free_fn = void (*)(void *);
    auto fn = reinterpret_cast<free_fn>(
        cpy::PyType_GetSlot(cpy::Py_TYPE(self), cpy::Py_tp_free));
    fn(self);
}

// Borrow the live embedded payload after a type check (exact type or subtype).
// On a type mismatch, set TypeError and throw MarshalError (the boundary catch
// returns the NULL sentinel, preserving the exception).
template <class T>
T *instance_payload(cpy::PyObject *o, cpy::PyTypeObject *type) {
    cpy::PyTypeObject *ot = cpy::Py_TYPE(o);
    if (ot != type && cpy::PyType_IsSubtype(ot, type) == 0) {
        cpy::PyErr_SetString(cpy::PyExc_TypeError,
                             "expected an instance of the exposed class");
        throw MarshalError{};
    }
    return &reinterpret_cast<Instance<T> *>(o)->payload;
}

// Mint a fresh instance of `type` and move `value` into its embedded payload.
// tp_alloc (PyType_GenericAlloc) zero-fills the storage (so `initialized` starts
// false); the placement-new over it is what actually constructs T. The nothrow
// requirement keeps this leak-free without a guard: a throwing move would
// orphan the freshly allocated PyObject (and the destructor can't run, the
// payload being half-built), so reject such a payload at instantiation instead.
template <class T>
cpy::PyObject *instance_to_py(cpy::PyTypeObject *type, T value) {
    static_assert(std::is_nothrow_move_constructible_v<T>,
                  "an exposed class payload must be nothrow-move-constructible "
                  "to cross the boundary by value without a leak on throw");
    cpy::PyObject *o = cpy::PyType_GenericAlloc(type, 0);
    if (o == nullptr) {
        throw MarshalError{};
    }
    auto *inst = reinterpret_cast<Instance<T> *>(o);
    new (&inst->payload) T(std::move(value));
    inst->initialized = true;
    return o;
}

}  // namespace tpy::interop
