/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef jit_PhoenixRecorder_h
#define jit_PhoenixRecorder_h

#ifdef JS_PHOENIX

#  include <stdint.h>
#  include <stdio.h>
#  include <type_traits>

#  include "jit/CacheIR.h"
#  include "jit/CacheIRGenerator.h"
#  include "jit/CacheIRWriter.h"
#  include "jit/ICState.h"
#  include "jit/PhoenixStubCode.h"
#  include "js/Printer.h"
#  include "js/TypeDecls.h"
#  include "js/Value.h"
#  include "vm/JSONPrinter.h"
#  include "vm/Opcodes.h"

namespace js {
namespace jit {
namespace phoenix {

// Records what SpiderMonkey's baseline IC pipeline does, so that the Lean
// model of it can be tested against it. Nothing is recorded unless
// PHOENIX_RECORD names a directory: each process then appends JSON Lines to
// PHOENIX_RECORD/<pid>.jsonl. PHOENIX_RECORD_KINDS, if set, is a
// comma-separated list of the CacheKinds to record, e.g. "UnaryArith".

bool ShouldRecord(CacheKind kind);

// One record, written as one JSON line: its version, its type (|record|), the
// process and the CacheKind, then whatever the caller adds through json(). The
// record file is locked from construction to destruction, so records from
// different threads don't interleave.
class MOZ_RAII RecordLine {
  FILE* file_;
  Fprinter out_;
  JSONPrinter json_;

 public:
  RecordLine(const char* record, CacheKind kind);
  ~RecordLine();

  JSONPrinter& json() { return json_; }
};

// One run of a generator.
class MOZ_RAII GeneratorRecord {
  RecordLine line_;

  void jsopArg(JSOp op);
  void cacheKindArg(CacheKind kind);
  void valueArg(const JS::Value& v);
  void boolArg(bool b);
  void intArg(int64_t i);
  void unrecordedArg();

 public:
  // |state| is the IC's state as the generator was constructed with it.
  GeneratorRecord(JSScript* script, jsbytecode* pc, CacheKind kind,
                  const ICState& state);

  void beginArgs();
  void endArgs();

  template <typename T>
  void arg(const T& v) {
    if constexpr (std::is_same_v<T, JSOp>) {
      jsopArg(v);
    } else if constexpr (std::is_same_v<T, CacheKind>) {
      cacheKindArg(v);
    } else if constexpr (std::is_convertible_v<const T&, const JS::Value&>) {
      valueArg(v);
    } else if constexpr (std::is_same_v<T, bool>) {
      boolArg(v);
    } else if constexpr (std::is_integral_v<T>) {
      intArg(int64_t(v));
    } else {
      unrecordedArg();
    }
  }

  // |attached| is whether the stub was added to the IC, which can fail even
  // when the generator decided to attach it.
  void outcome(AttachDecision decision, bool attached, const char* stubName,
               const CacheIRWriter& writer);
};

// Records a run of a generator from TryAttachStub. |Args| are TryAttachStub's,
// and |args| the arguments it constructed the generator with after the IC
// state, which are the generator's inputs. |state| is the IC's state before
// the stub was attached.
template <typename Generator, typename... Args>
void RecordGenerator(JSScript* script, jsbytecode* pc, const ICState& state,
                     const Generator& gen, AttachDecision decision,
                     bool attached,
                     const std::remove_reference_t<Args>&... args) {
  static_assert(
      ((std::is_lvalue_reference_v<Args> ||
        std::is_trivially_copyable_v<std::remove_reference_t<Args>>) &&
       ...),
      "The generator may have been constructed by moving from an argument");

  if (!ShouldRecord(gen.cacheKind())) {
    return;
  }
  GeneratorRecord record(script, pc, gen.cacheKind(), state);
  record.beginArgs();
  (record.arg(args), ...);
  record.endArgs();
  record.outcome(decision, attached, gen.stubName(), gen.writerRef());
}

// Records the compilation of |writer| to |code| by the baseline CacheIR
// compiler. |relocationBytes| is the size of |code|'s relocation tables, and
// |replay| what the replay table did with it: nullptr without a table, or
// "substituted", "notInTable" or "refused".
void RecordCompile(CacheKind kind, const char* stubName,
                   const CacheIRWriter& writer, const StubCodeInfo& info,
                   size_t relocationBytes, bool makesGCCalls, JitCode* code,
                   const char* replay);

}  // namespace phoenix
}  // namespace jit
}  // namespace js

#endif /* JS_PHOENIX */

#endif /* jit_PhoenixRecorder_h */
