/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "jit/PhoenixRecorder.h"

#include "mozilla/Assertions.h"
#include "mozilla/Sprintf.h"

#include <inttypes.h>
#include <stdlib.h>
#include <string.h>

#include "jit/JitCode.h"
#include "jit/JitOptions.h"
#include "js/ColumnNumber.h"
#include "util/GetPidProvider.h"
#include "vm/BytecodeUtil.h"
#include "vm/JSScript.h"

#if defined(JS_CODEGEN_ARM)
#  include "jit/arm/Architecture-arm.h"
#elif defined(JS_CODEGEN_X86) || defined(JS_CODEGEN_X64)
#  include "jit/Assembler.h"
#endif

using namespace js;
using namespace js::jit;
using namespace js::jit::phoenix;

// The version of the record format, in every record's "v".
static constexpr int RecordVersion = 5;

// Records give these enums by number, so a renumbering must not go unnoticed.
static_assert(uint8_t(StubField::Type::Limit) == 17);
static_assert(JSVAL_TYPE_UNKNOWN == 0x20);

#if defined(JS_CODEGEN_X86)
static constexpr const char* BackendName = "x86";
#elif defined(JS_CODEGEN_X64)
static constexpr const char* BackendName = "x64";
#elif defined(JS_CODEGEN_ARM)
static constexpr const char* BackendName = "arm";
#elif defined(JS_CODEGEN_ARM64)
static constexpr const char* BackendName = "arm64";
#elif defined(JS_CODEGEN_MIPS64)
static constexpr const char* BackendName = "mips64";
#elif defined(JS_CODEGEN_LOONG64)
static constexpr const char* BackendName = "loong64";
#elif defined(JS_CODEGEN_RISCV64)
static constexpr const char* BackendName = "riscv64";
#elif defined(JS_CODEGEN_WASM32)
static constexpr const char* BackendName = "wasm32";
#elif defined(JS_CODEGEN_NONE)
static constexpr const char* BackendName = "none";
#else
#  error "Phoenix: name this backend in the process record"
#endif

static const char* Bool(bool b) { return b ? "true" : "false"; }

// The build that wrote the records: its backend, whether it's a debug build or
// a simulator's, and the CPU features the JIT read at startup (ARMFlags on
// ARM, CPUInfo on x86 and x64).
static void WriteBuild(GenericPrinter& out) {
#ifdef DEBUG
  constexpr bool debug = true;
#else
  constexpr bool debug = false;
#endif
#ifdef JS_SIMULATOR
  constexpr bool simulator = true;
#else
  constexpr bool simulator = false;
#endif
  out.printf("\"build\":{\"backend\":\"%s\",\"debug\":%s,\"simulator\":%s",
             BackendName, Bool(debug), Bool(simulator));
#if defined(JS_CODEGEN_ARM)
  out.printf(
      ",\"armFlags\":{\"HasARMv7\":%s,\"HasMOVWT\":%s,\"HasLDSTREXBHD\":%s,"
      "\"HasDMBDSBISB\":%s,\"HasVFP\":%s,\"Has32DP\":%s,\"HasVFPv3\":%s,"
      "\"HasVFPv4\":%s,\"HasNEON\":%s,\"HasIDIV\":%s,"
      "\"HasFPHalfPrecision\":%s,\"HasAlignmentFault\":%s,"
      "\"UseHardFpABI\":%s}",
      Bool(ARMFlags::HasARMv7()), Bool(ARMFlags::HasMOVWT()),
      Bool(ARMFlags::HasLDSTREXBHD()), Bool(ARMFlags::HasDMBDSBISB()),
      Bool(ARMFlags::HasVFP()), Bool(ARMFlags::Has32DP()),
      Bool(ARMFlags::HasVFPv3()), Bool(ARMFlags::HasVFPv4()),
      Bool(ARMFlags::HasNEON()), Bool(ARMFlags::HasIDIV()),
      Bool(ARMFlags::HasFPHalfPrecision()), Bool(ARMFlags::HasAlignmentFault()),
      Bool(ARMFlags::UseHardFpABI()));
#elif defined(JS_CODEGEN_X86) || defined(JS_CODEGEN_X64)
  out.printf(
      ",\"x86CPUInfo\":{\"IsSSE2Present\":%s,\"IsSSE3Present\":%s,"
      "\"IsSSSE3Present\":%s,\"IsSSE41Present\":%s,\"IsSSE42Present\":%s,"
      "\"IsPOPCNTPresent\":%s,\"IsBMI1Present\":%s,\"IsBMI2Present\":%s,"
      "\"IsLZCNTPresent\":%s,\"IsAVXPresent\":%s,\"IsAVX2Present\":%s,"
      "\"IsFMAPresent\":%s,\"IsF16CPresent\":%s}",
      Bool(CPUInfo::IsSSE2Present()), Bool(CPUInfo::IsSSE3Present()),
      Bool(CPUInfo::IsSSSE3Present()), Bool(CPUInfo::IsSSE41Present()),
      Bool(CPUInfo::IsSSE42Present()), Bool(CPUInfo::IsPOPCNTPresent()),
      Bool(CPUInfo::IsBMI1Present()), Bool(CPUInfo::IsBMI2Present()),
      Bool(CPUInfo::IsLZCNTPresent()), Bool(CPUInfo::IsAVXPresent()),
      Bool(CPUInfo::IsAVX2Present()), Bool(CPUInfo::IsFMAPresent()),
      Bool(CPUInfo::IsF16CPresent()));
#endif
  out.put("}");
}

static void WriteJSONString(GenericPrinter& out, const char* s) {
  JSONEscape esc;
  EscapePrinter<GenericPrinter, JSONEscape> escaped(out, esc);
  out.putChar('"');
  escaped.put(s);
  out.putChar('"');
}

// The first line of each record file: the process's command line, which tells
// which test the records came from, the build, the JIT options that change the
// code of stubs, and the replay table.
static void WriteProcessRecord(FILE* file) {
  Fprinter out(file);
  out.printf("{\"v\":%d,\"record\":\"process\",\"pid\":%d,\"argv\":[",
             RecordVersion, int(getpid()));
  if (FILE* cmdline = fopen("/proc/self/cmdline", "r")) {
    JSONEscape esc;
    bool first = true;
    bool inArg = false;
    for (int c; (c = getc(cmdline)) != EOF;) {
      if (!inArg) {
        out.put(first ? "\"" : ",\"");
        first = false;
        inArg = true;
      }
      if (c == '\0') {
        out.putChar('"');
        inArg = false;
      } else if (esc.isSafeChar(char16_t(c))) {
        out.putChar(char(c));
      } else {
        esc.convertInto(out, char16_t(c));
      }
    }
    if (inArg) {
      out.putChar('"');
    }
    fclose(cmdline);
  }
  out.put("],");
  WriteBuild(out);
  out.printf(
      ",\"jitOptions\":{\"icFramePointers\":%s,\"spectreIndexMasking\":%s,"
      "\"spectreObjectMitigations\":%s,\"spectreStringMitigations\":%s,"
      "\"spectreValueMasking\":%s,\"spectreJitToCxxCalls\":%s},\"replay\":",
      JitOptions.enableICFramePointers ? "true" : "false",
      JitOptions.spectreIndexMasking ? "true" : "false",
      JitOptions.spectreObjectMitigations ? "true" : "false",
      JitOptions.spectreStringMitigations ? "true" : "false",
      JitOptions.spectreValueMasking ? "true" : "false",
      JitOptions.spectreJitToCxxCalls ? "true" : "false");
  const char* replay = getenv("PHOENIX_REPLAY");
  if (replay && *replay) {
    WriteJSONString(out, replay);
  } else {
    out.put("null");
  }
  out.put("}\n");
  out.flush();
}

// The process's record file, and the kinds to record, from PHOENIX_RECORD and
// PHOENIX_RECORD_KINDS. A misspelt setting crashes, rather than silently
// recording nothing.
struct Recorder {
  FILE* file = nullptr;
  bool recordKind[NumCacheKinds] = {};

  Recorder();
};

Recorder::Recorder() {
  const char* dir = getenv("PHOENIX_RECORD");
  if (!dir || !*dir) {
    return;
  }

  char path[4096];
  SprintfLiteral(path, "%s/%d.jsonl", dir, int(getpid()));
  file = fopen(path, "a");
  if (!file) {
    fprintf(stderr, "PHOENIX_RECORD: can't open %s\n", path);
    MOZ_CRASH("PHOENIX_RECORD: can't open the record file");
  }
  WriteProcessRecord(file);

  const char* kinds = getenv("PHOENIX_RECORD_KINDS");
  if (!kinds) {
    for (bool& record : recordKind) {
      record = true;
    }
    return;
  }
  const char* name = kinds;
  while (*name) {
    size_t len = strcspn(name, ",");
    bool found = false;
    for (size_t i = 0; i < NumCacheKinds; i++) {
      if (strlen(CacheKindNames[i]) == len &&
          strncmp(CacheKindNames[i], name, len) == 0) {
        recordKind[i] = true;
        found = true;
      }
    }
    if (!found) {
      fprintf(stderr, "PHOENIX_RECORD_KINDS: no CacheKind named '%.*s'\n",
              int(len), name);
      MOZ_CRASH("PHOENIX_RECORD_KINDS: unknown CacheKind");
    }
    name += len;
    if (*name == ',') {
      name++;
    }
  }
}

static Recorder& GetRecorder() {
  static Recorder recorder;
  return recorder;
}

bool phoenix::ShouldRecord(CacheKind kind) {
  Recorder& recorder = GetRecorder();
  return recorder.file && recorder.recordKind[size_t(kind)];
}

static const char* ModeName(ICState::Mode mode) {
  switch (mode) {
    case ICState::Mode::Specialized:
      return "Specialized";
    case ICState::Mode::Megamorphic:
      return "Megamorphic";
    case ICState::Mode::Generic:
      return "Generic";
  }
  MOZ_CRASH("Unknown ICState::Mode");
}

static const char* DecisionName(AttachDecision decision) {
  switch (decision) {
    case AttachDecision::NoAction:
      return "NoAction";
    case AttachDecision::Attach:
      return "Attach";
    case AttachDecision::TemporarilyUnoptimizable:
      return "TemporarilyUnoptimizable";
    case AttachDecision::Deferred:
      return "Deferred";
  }
  MOZ_CRASH("Unknown AttachDecision");
}

static const char* ValueTypeName(JS::ValueType type) {
  switch (type) {
    case JS::ValueType::Double:
      return "double";
    case JS::ValueType::Int32:
      return "int32";
    case JS::ValueType::Boolean:
      return "boolean";
    case JS::ValueType::Undefined:
      return "undefined";
    case JS::ValueType::Null:
      return "null";
    case JS::ValueType::Magic:
      return "magic";
    case JS::ValueType::String:
      return "string";
    case JS::ValueType::Symbol:
      return "symbol";
    case JS::ValueType::PrivateGCThing:
      return "privateGCThing";
    case JS::ValueType::BigInt:
      return "bigint";
    case JS::ValueType::Object:
      return "object";
  }
  MOZ_CRASH("Unknown JS::ValueType");
}

static void WriteHex(JSONPrinter& json, const char* name, const uint8_t* start,
                     size_t length) {
  GenericPrinter& p = json.beginStringProperty(name);
  for (size_t i = 0; i < length; i++) {
    p.printf("%02x", start[i]);
  }
  json.endStringProperty();
}

// The "cacheir" property: |writer|'s CacheIR as bytes, and the writer's state
// the bytes don't hold. With |withStubFieldValues|, its stub fields' values.
static void WriteCacheIR(JSONPrinter& json, const CacheIRWriter& writer,
                         bool withStubFieldValues) {
  if (writer.failed() || writer.codeLength() == 0) {
    json.nullProperty("cacheir");
    return;
  }

  json.beginObjectProperty("cacheir");
  WriteHex(json, "bytes", writer.codeStart(), writer.codeLength());
  json.beginObjectProperty("writer");
  json.property("numInputOperands", writer.numInputOperands());
  json.property("numOperandIds", writer.numOperandIds());
  json.property("numInstructions", writer.numInstructions());

  // operandLastUsed_'s entries, as many as it has. writeOperandId resizes it
  // up to the operand it writes, so the entries are a prefix of the operands,
  // and an operand has one exactly when it's dead after every instruction.
  json.beginListProperty("operandLastUsed");
  uint32_t numEntries = 0;
  while (numEntries < writer.numOperandIds() &&
         writer.operandIsDead(numEntries, UINT32_MAX)) {
    json.value(int(writer.operandLastUsed(numEntries)));
    numEntries++;
  }
#ifdef DEBUG
  for (uint32_t id = numEntries; id < writer.numOperandIds(); id++) {
    MOZ_ASSERT(!writer.operandIsDead(id, UINT32_MAX));
  }
#endif
  json.endList();

  json.property("stubDataSize", uint64_t(writer.stubDataSize()));
  json.property("typeData", uint32_t(writer.typeData().type()));
  json.beginListProperty("stubFields");
  for (size_t i = 0; i < writer.numStubFields(); i++) {
    const StubField& field = writer.stubField(i);
    json.beginObject();
    json.property("type", uint32_t(field.type()));
    if (withStubFieldValues) {
      json.formatProperty("value", "0x%016" PRIx64, field.rawData());
    }
    json.endObject();
  }
  json.endList();
  json.endObject();
  json.endObject();
}

RecordLine::RecordLine(const char* record, CacheKind kind)
    : file_(GetRecorder().file),
      out_(file_),
      json_(out_, /* indent = */ false) {
  MOZ_ASSERT(file_);
  flockfile(file_);

  json_.beginObject();
  json_.property("v", RecordVersion);
  json_.property("record", record);
  json_.property("pid", int32_t(getpid()));
  json_.property("kind", CacheKindNames[size_t(kind)]);
}

RecordLine::~RecordLine() {
  json_.endObject();
  out_.put("\n");
  out_.flush();
  funlockfile(file_);
}

GeneratorRecord::GeneratorRecord(JSScript* script, jsbytecode* pc,
                                 CacheKind kind, const ICState& state)
    : line_("generator", kind) {
  JSONPrinter& json = line_.json();
  json.beginObjectProperty("loc");
  {
    const char* filename = script->filename();
    GenericPrinter& p = json.beginStringProperty("file");
    JSONEscape esc;
    EscapePrinter<GenericPrinter, JSONEscape> escaped(p, esc);
    escaped.put(filename ? filename : "");
    json.endStringProperty();
  }
  JS::LimitedColumnNumberOneOrigin column;
  unsigned line = PCToLineNumber(script, pc, &column);
  json.property("line", uint32_t(line));
  json.property("column", column.oneOriginValue());
  json.property("pcOffset", uint32_t(script->pcToOffset(pc)));
  json.property("op", CodeName(JSOp(*pc)));
  json.endObject();

  json.beginObjectProperty("state");
  json.property("mode", ModeName(state.mode()));
  json.boolProperty("isFirstStub", state.newStubIsFirstStub());
  json.property("numOptimizedStubs", uint32_t(state.numOptimizedStubs()));
  json.endObject();
}

void GeneratorRecord::beginArgs() { line_.json().beginListProperty("args"); }

void GeneratorRecord::endArgs() { line_.json().endList(); }

void GeneratorRecord::jsopArg(JSOp op) {
  JSONPrinter& json = line_.json();
  json.beginObject();
  json.property("type", "JSOp");
  json.property("name", CodeName(op));
  json.property("byte", uint32_t(op));
  json.endObject();
}

void GeneratorRecord::cacheKindArg(CacheKind kind) {
  JSONPrinter& json = line_.json();
  json.beginObject();
  json.property("type", "CacheKind");
  json.property("name", CacheKindNames[size_t(kind)]);
  json.endObject();
}

void GeneratorRecord::valueArg(const JS::Value& v) {
  JSONPrinter& json = line_.json();
  json.beginObject();
  json.property("type", "Value");
  json.formatProperty("bits", "0x%016" PRIx64, v.asRawBits());
  json.property("tag", ValueTypeName(v.type()));
  json.endObject();
}

void GeneratorRecord::boolArg(bool b) {
  JSONPrinter& json = line_.json();
  json.beginObject();
  json.property("type", "bool");
  json.boolProperty("value", b);
  json.endObject();
}

void GeneratorRecord::intArg(int64_t i) {
  JSONPrinter& json = line_.json();
  json.beginObject();
  json.property("type", "int");
  json.property("value", i);
  json.endObject();
}

void GeneratorRecord::unrecordedArg() {
  JSONPrinter& json = line_.json();
  json.beginObject();
  json.property("type", "unrecorded");
  json.endObject();
}

void GeneratorRecord::outcome(AttachDecision decision, bool attached,
                              const char* stubName,
                              const CacheIRWriter& writer) {
  JSONPrinter& json = line_.json();
  json.property("decision", DecisionName(decision));
  json.boolProperty("attached", attached);
  if (stubName) {
    json.property("stubName", stubName);
  } else {
    json.nullProperty("stubName");
  }
  json.boolProperty("writerFailed", writer.failed());
  WriteCacheIR(json, writer, /* withStubFieldValues = */ true);
}

void phoenix::RecordCompile(CacheKind kind, const char* stubName,
                            const CacheIRWriter& writer,
                            const StubCodeInfo& info, size_t relocationBytes,
                            bool makesGCCalls, JitCode* code,
                            const char* replay) {
  RecordLine line("compile", kind);
  JSONPrinter& json = line.json();
  if (stubName) {
    json.property("stubName", stubName);
  } else {
    json.nullProperty("stubName");
  }
  WriteCacheIR(json, writer, /* withStubFieldValues = */ false);

  json.beginObjectProperty("code");
  WriteHex(json, "bytes", code->raw(), code->instructionsSize());
  json.beginListProperty("ops");
  for (size_t i = 0; i < info.numOps; i++) {
    json.beginObject();
    json.property("op", CacheIRCodeName(info.ops[i].op));
    json.property("offset", info.ops[i].offset);
    json.endObject();
  }
  json.endList();
  json.boolProperty("tooManyOps", info.tooManyOps);
  json.property("returnOffset", info.returnOffset);
  json.property("failurePathsOffset", info.failurePathsOffset);
  json.property("relocationBytes", uint64_t(relocationBytes));
  json.property("localTracingSlots", uint32_t(code->localTracingSlots()));
  json.boolProperty("makesGCCalls", makesGCCalls);
  json.endObject();

  if (replay) {
    json.property("replay", replay);
  } else {
    json.nullProperty("replay");
  }
}
