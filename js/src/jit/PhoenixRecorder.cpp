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
#include "js/ColumnNumber.h"
#include "util/GetPidProvider.h"
#include "vm/BytecodeUtil.h"
#include "vm/JSScript.h"

using namespace js;
using namespace js::jit;
using namespace js::jit::phoenix;

static constexpr size_t NumCacheKinds = 0
#define COUNT_KIND(kind) +1
    CACHE_IR_KINDS(COUNT_KIND)
#undef COUNT_KIND
    ;

// The first line of each record file: the process's command line, which tells
// which test the records came from.
static void WriteProcessRecord(FILE* file) {
  Fprinter out(file);
  out.printf("{\"v\":1,\"record\":\"process\",\"pid\":%d,\"argv\":[",
             int(getpid()));
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
  out.put("]}\n");
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

GeneratorRecord::GeneratorRecord(JSScript* script, jsbytecode* pc,
                                 CacheKind kind, const ICState& state)
    : kind_(kind),
      file_(GetRecorder().file),
      out_(file_),
      json_(out_, /* indent = */ false) {
  MOZ_ASSERT(file_);
  flockfile(file_);

  json_.beginObject();
  json_.property("v", 1);
  json_.property("record", "generator");
  json_.property("pid", int32_t(getpid()));
  json_.property("kind", CacheKindNames[size_t(kind)]);

  json_.beginObjectProperty("loc");
  {
    const char* filename = script->filename();
    GenericPrinter& p = json_.beginStringProperty("file");
    JSONEscape esc;
    EscapePrinter<GenericPrinter, JSONEscape> escaped(p, esc);
    escaped.put(filename ? filename : "");
    json_.endStringProperty();
  }
  JS::LimitedColumnNumberOneOrigin column;
  unsigned line = PCToLineNumber(script, pc, &column);
  json_.property("line", uint32_t(line));
  json_.property("column", column.oneOriginValue());
  json_.property("pcOffset", uint32_t(script->pcToOffset(pc)));
  json_.property("op", CodeName(JSOp(*pc)));
  json_.endObject();

  json_.beginObjectProperty("state");
  json_.property("mode", ModeName(state.mode()));
  json_.boolProperty("isFirstStub", state.newStubIsFirstStub());
  json_.property("numOptimizedStubs", uint32_t(state.numOptimizedStubs()));
  json_.endObject();
}

GeneratorRecord::~GeneratorRecord() {
  json_.endObject();
  out_.put("\n");
  out_.flush();
  funlockfile(file_);
}

void GeneratorRecord::beginArgs() { json_.beginListProperty("args"); }

void GeneratorRecord::endArgs() { json_.endList(); }

void GeneratorRecord::jsopArg(JSOp op) {
  json_.beginObject();
  json_.property("type", "JSOp");
  json_.property("name", CodeName(op));
  json_.property("byte", uint32_t(op));
  json_.endObject();
}

void GeneratorRecord::cacheKindArg(CacheKind kind) {
  json_.beginObject();
  json_.property("type", "CacheKind");
  json_.property("name", CacheKindNames[size_t(kind)]);
  json_.endObject();
}

void GeneratorRecord::valueArg(const JS::Value& v) {
  json_.beginObject();
  json_.property("type", "Value");
  json_.formatProperty("bits", "0x%016" PRIx64, v.asRawBits());
  json_.property("tag", ValueTypeName(v.type()));
  json_.endObject();
}

void GeneratorRecord::boolArg(bool b) {
  json_.beginObject();
  json_.property("type", "bool");
  json_.boolProperty("value", b);
  json_.endObject();
}

void GeneratorRecord::intArg(int64_t i) {
  json_.beginObject();
  json_.property("type", "int");
  json_.property("value", i);
  json_.endObject();
}

void GeneratorRecord::unrecordedArg() {
  json_.beginObject();
  json_.property("type", "unrecorded");
  json_.endObject();
}

void GeneratorRecord::outcome(AttachDecision decision, bool attached,
                              const char* stubName,
                              const CacheIRWriter& writer) {
  json_.property("decision", DecisionName(decision));
  json_.boolProperty("attached", attached);
  if (stubName) {
    json_.property("stubName", stubName);
  } else {
    json_.nullProperty("stubName");
  }

  json_.boolProperty("writerFailed", writer.failed());
  if (writer.failed() || writer.codeLength() == 0) {
    json_.nullProperty("cacheir");
    return;
  }

  json_.beginObjectProperty("cacheir");
  {
    GenericPrinter& p = json_.beginStringProperty("aot");
    AOTEscape esc;
    EscapePrinter<GenericPrinter, AOTEscape> escaped(p, esc);
    SpewCacheIROpsAsAOT(escaped, kind_, writer);
    json_.endStringProperty();
  }
  {
    GenericPrinter& p = json_.beginStringProperty("bytes");
    for (const uint8_t* b = writer.codeStart(); b != writer.codeEnd(); b++) {
      p.printf("%02x", *b);
    }
    json_.endStringProperty();
  }
  json_.beginListProperty("stubFields");
  for (size_t i = 0; i < writer.numStubFields(); i++) {
    const StubField& field = writer.stubField(i);
    json_.beginObject();
    json_.property("type", StubFieldTypeName(field.type()));
    json_.formatProperty("value", "0x%016" PRIx64, field.rawData());
    json_.endObject();
  }
  json_.endList();
  json_.endObject();
}
