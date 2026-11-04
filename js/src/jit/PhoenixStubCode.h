/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef jit_PhoenixStubCode_h
#define jit_PhoenixStubCode_h

#ifdef JS_PHOENIX

#  include "mozilla/Array.h"

#  include <stddef.h>
#  include <stdint.h>

#  include "jit/CacheIR.h"
#  include "js/TypeDecls.h"

namespace js {
namespace jit {

class BaselineCacheIRCompiler;
class CacheIRWriter;
class JitCode;

namespace phoenix {

static constexpr size_t NumCacheKinds = 0
#  define COUNT_KIND(kind) +1
    CACHE_IR_KINDS(COUNT_KIND)
#  undef COUNT_KIND
    ;

// What BaselineCacheIRCompiler::compile records about the code of a stub while
// it emits it. Offsets are from the start of the code: the ops' code is
// [ops[i].offset, ops[i + 1].offset), the last op's ends at returnOffset, and
// the failure paths start at failurePathsOffset. A fixed array, so that
// recording never allocates.
struct StubCodeInfo {
  struct Op {
    CacheOp op;
    uint32_t offset;
  };
  static constexpr size_t MaxOps = 256;

  mozilla::Array<Op, MaxOps> ops;
  size_t numOps = 0;
  bool tooManyOps = false;
  uint32_t returnOffset = 0;
  uint32_t failurePathsOffset = 0;

  void addOp(CacheOp op, uint32_t offset) {
    if (numOps == MaxOps) {
      tooManyOps = true;
      return;
    }
    ops[numOps++] = {op, offset};
  }
};

// Called by LookupOrCompileStub when |comp| has compiled |writer| to |code|,
// before the code is cached. Records the compilation (PHOENIX_RECORD), and, if
// the replay table (PHOENIX_REPLAY) has code for this kind and CacheIR,
// replaces |code| with it. Returns false on OOM, like compile().
[[nodiscard]] bool OnBaselineStubCompiled(JSContext* cx, CacheKind kind,
                                          const CacheIRWriter& writer,
                                          const char* stubName,
                                          BaselineCacheIRCompiler& comp,
                                          JitCode*& code);

}  // namespace phoenix
}  // namespace jit
}  // namespace js

#endif /* JS_PHOENIX */

#endif /* jit_PhoenixStubCode_h */
