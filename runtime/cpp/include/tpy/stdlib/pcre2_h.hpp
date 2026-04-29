#pragma once
// Hand-written PCRE2 facade for TPy bindings.
//
// We deliberately do NOT `#include <pcre2.h>` here. PCRE2's header
// `#define`s a large set of `PCRE2_*` macros that would collide with
// TPy module-level constants of the same name (the preprocessor expands
// the macro at every TPy `inline constexpr uint32_t PCRE2_CASELESS = ...;`
// declaration site, producing a syntax error). By instead manually
// mirroring the symbols and types we need, pcre2.h never enters any
// TPy-generated translation unit -- its macros never get a chance to fire.
//
// All names match upstream pcre2.h exactly and live at global scope (same
// as the real header), so generated TPy code that references e.g.
// `pcre2_code_8*` or `PCRE2_SPTR8` resolves directly without any namespace
// qualification.
//
// PCRE2's ABI is stable, so this mirror is low-maintenance. Bumping the
// vendored PCRE2 version doesn't require touching this file unless the
// upstream API surface we use changes shape.
//
// Linker connects our extern "C" declarations to the symbols defined by
// the vendored PCRE2 .c files (compiled separately into their own .o
// files; their TUs include the real pcre2.h).

#include <cstddef>
#include <cstdint>

// ---- Type aliases (8-bit code unit) ----
// Upstream pcre2.h has `typedef size_t PCRE2_SIZE`. We use `std::uint64_t`
// instead because the TPy bindings (lib/tpy/_bindings/pcre2.py) type the
// matching pointer params as `Ptr[UInt64]` -- on Linux x86_64 `size_t` and
// `uint64_t` are both `unsigned long`, but on macOS `size_t` is
// `unsigned long` while `uint64_t` is `unsigned long long`, so the pointer
// types don't implicitly convert. Both are 8-byte unsigned on every 64-bit
// Unix target we support, so this matches PCRE2's actual ABI byte-for-byte;
// the linker only resolves the `extern "C"` symbol name, not param types.
using PCRE2_SIZE   = std::uint64_t;
using PCRE2_SPTR8  = const std::uint8_t*;
using PCRE2_UCHAR8 = std::uint8_t;

// ---- Opaque handle types ----
// Forward declarations only -- we only ever pass pointers around, never
// dereference or sizeof the underlying structs in TPy-generated code.
struct pcre2_real_code_8;
struct pcre2_real_match_data_8;
struct pcre2_real_match_context_8;
struct pcre2_real_general_context_8;
struct pcre2_real_compile_context_8;

using pcre2_code_8            = pcre2_real_code_8;
using pcre2_match_data_8      = pcre2_real_match_data_8;
using pcre2_match_context_8   = pcre2_real_match_context_8;
using pcre2_general_context_8 = pcre2_real_general_context_8;
using pcre2_compile_context_8 = pcre2_real_compile_context_8;

// ---- Function declarations ----
extern "C" {

pcre2_code_8* pcre2_compile_8(PCRE2_SPTR8 pattern, PCRE2_SIZE length,
                              std::uint32_t options, int* errorcode,
                              PCRE2_SIZE* erroffset,
                              pcre2_compile_context_8* ccontext);

void pcre2_code_free_8(pcre2_code_8* code);

int pcre2_jit_compile_8(pcre2_code_8* code, std::uint32_t options);

int pcre2_get_error_message_8(int errorcode, PCRE2_UCHAR8* buffer,
                              PCRE2_SIZE bufflen);

pcre2_match_context_8* pcre2_match_context_create_8(
    pcre2_general_context_8* gcontext);
void pcre2_match_context_free_8(pcre2_match_context_8* mcontext);

pcre2_match_data_8* pcre2_match_data_create_from_pattern_8(
    const pcre2_code_8* code, pcre2_general_context_8* gcontext);
void pcre2_match_data_free_8(pcre2_match_data_8* md);

int pcre2_match_8(const pcre2_code_8* code, PCRE2_SPTR8 subject,
                  PCRE2_SIZE length, PCRE2_SIZE startoffset,
                  std::uint32_t options, pcre2_match_data_8* match_data,
                  pcre2_match_context_8* mcontext);

PCRE2_SIZE* pcre2_get_ovector_pointer_8(pcre2_match_data_8* md);

int pcre2_pattern_info_8(const pcre2_code_8* code, std::uint32_t what,
                         void* where);

int pcre2_substitute_8(const pcre2_code_8* code, PCRE2_SPTR8 subject,
                       PCRE2_SIZE length, PCRE2_SIZE startoffset,
                       std::uint32_t options, pcre2_match_data_8* match_data,
                       pcre2_match_context_8* mcontext,
                       PCRE2_SPTR8 replacement, PCRE2_SIZE rlength,
                       PCRE2_UCHAR8* outputbuffer, PCRE2_SIZE* outlengthptr);

}  // extern "C"
