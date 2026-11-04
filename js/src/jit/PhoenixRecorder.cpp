/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "jit/PhoenixRecorder.h"

#include "mozilla/Assertions.h"
#include "mozilla/Sprintf.h"

#include <inttypes.h>
#include <stdlib.h>
#include <string.h>

#include "jit/CacheIRSpewer.h"
#include "jit/JitCode.h"
#include "jit/JitOptions.h"
#include "js/ColumnNumber.h"
#include "util/GetPidProvider.h"
#include "vm/BytecodeUtil.h"
#include "vm/JSScript.h"

using namespace js;
using namespace js::jit;
using namespace js::jit::phoenix;

// The version of the record format, in every record's "v".
static constexpr int RecordVersion = 2;

static void WriteJSONString(GenericPrinter& out, const char* s) {
  JSONEscape esc;
  EscapePrinter<GenericPrinter, JSONEscape> escaped(out, esc);
  out.putChar('"');
  escaped.put(s);
  out.putChar('"');
}

// The first line of each record file: the process's command line, which tells
// which test the records came from, the JIT options that change the code of
// stubs, and the replay table.
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
  out.printf(
      "],\"jitOptions\":{\"icFramePointers\":%s,\"spectreIndexMasking\":%s,"
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

// JSON-escapes the AOT text of a stub's CacheIR, dropping the newline that
// ends it.
class AOTEscape {
  JSONEscape json_;

 public:
  bool isSafeChar(char16_t c) { return c != '\n' && json_.isSafeChar(c); }
  void convertInto(GenericPrinter& out, char16_t c) {
    if (c != '\n') {
      json_.convertInto(out, c);
    }
  }
};

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

// The "cacheir" property: |writer|'s CacheIR as AOT text and as bytes, and with
// |withStubFields|, its stub fields' values.
static void WriteCacheIR(JSONPrinter& json, CacheKind kind,
                         const CacheIRWriter& writer, bool withStubFields) {
  if (writer.failed() || writer.codeLength() == 0) {
    json.nullProperty("cacheir");
    return;
  }

  json.beginObjectProperty("cacheir");
  {
    GenericPrinter& p = json.beginStringProperty("aot");
    AOTEscape esc;
    EscapePrinter<GenericPrinter, AOTEscape> escaped(p, esc);
    SpewCacheIROpsAsAOT(escaped, kind, writer);
    json.endStringProperty();
  }
  WriteHex(json, "bytes", writer.codeStart(), writer.codeLength());
  if (withStubFields) {
    json.beginListProperty("stubFields");
    for (size_t i = 0; i < writer.numStubFields(); i++) {
      const StubField& field = writer.stubField(i);
      json.beginObject();
      json.property("type", StubFieldTypeName(field.type()));
      json.formatProperty("value", "0x%016" PRIx64, field.rawData());
      json.endObject();
    }
    json.endList();
  }
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
    : kind_(kind), line_("generator", kind) {
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
  WriteCacheIR(json, kind_, writer, /* withStubFields = */ true);
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
  WriteCacheIR(json, kind, writer, /* withStubFields = */ false);

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
