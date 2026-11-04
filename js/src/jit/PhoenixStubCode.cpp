/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "jit/PhoenixStubCode.h"

#include "mozilla/Assertions.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <utility>

#include "jit/BaselineCacheIRCompiler.h"
#include "jit/CacheIRWriter.h"
#include "jit/JitAllocPolicy.h"
#include "jit/JitCode.h"
#include "jit/JitOptions.h"
#include "jit/Linker.h"
#include "jit/MacroAssembler.h"
#include "jit/PhoenixRecorder.h"
#include "js/AllocPolicy.h"
#include "js/Utility.h"
#include "js/Vector.h"
#include "vm/JSContext.h"

using namespace js;
using namespace js::jit;
using namespace js::jit::phoenix;

using ByteVector = Vector<uint8_t, 0, SystemAllocPolicy>;

// The code to use in place of the baseline CacheIR compiler's for one kind and
// CacheIR.
struct ReplayEntry {
  CacheKind kind;
  ByteVector cacheIR;
  ByteVector code;
};

// The replay table named by PHOENIX_REPLAY, loaded on first use. A table that
// can't be read or parsed crashes, rather than silently replacing nothing.
//
// Each line is a CacheKind's name, the CacheIR bytes and the code bytes, in
// hex, separated by spaces. Blank lines and lines starting with '#' are
// ignored. See phoenix-docs/docs/substitution.md.
struct ReplayTable {
  bool enabled = false;
  Vector<ReplayEntry, 0, SystemAllocPolicy> entries;

  ReplayTable();

  const ReplayEntry* find(CacheKind kind, const uint8_t* cacheIR,
                          size_t length) const {
    for (const ReplayEntry& entry : entries) {
      if (entry.kind == kind && entry.cacheIR.length() == length &&
          memcmp(entry.cacheIR.begin(), cacheIR, length) == 0) {
        return &entry;
      }
    }
    return nullptr;
  }

 private:
  void parseLine(const char* path, size_t lineNumber, const char* line,
                 size_t length, AutoEnterOOMUnsafeRegion& oomUnsafe);
};

[[noreturn]] static void ReplayError(const char* path, size_t lineNumber,
                                     const char* message) {
  fprintf(stderr, "PHOENIX_REPLAY: %s:%zu: %s\n", path, lineNumber, message);
  MOZ_CRASH("PHOENIX_REPLAY: bad replay table");
}

static int HexDigit(char c) {
  if (c >= '0' && c <= '9') {
    return c - '0';
  }
  if (c >= 'a' && c <= 'f') {
    return c - 'a' + 10;
  }
  if (c >= 'A' && c <= 'F') {
    return c - 'A' + 10;
  }
  return -1;
}

// Decodes |length| hex digits at |hex| into |out|. Returns false if they
// aren't an even number of hex digits.
static bool ParseHex(const char* hex, size_t length, ByteVector& out,
                     AutoEnterOOMUnsafeRegion& oomUnsafe) {
  if (length % 2 != 0) {
    return false;
  }
  for (size_t i = 0; i < length; i += 2) {
    int high = HexDigit(hex[i]);
    int low = HexDigit(hex[i + 1]);
    if (high < 0 || low < 0) {
      return false;
    }
    if (!out.append(uint8_t(high * 16 + low))) {
      oomUnsafe.crash("PHOENIX_REPLAY");
    }
  }
  return true;
}

ReplayTable::ReplayTable() {
  const char* path = getenv("PHOENIX_REPLAY");
  if (!path || !*path) {
    return;
  }
  enabled = true;

  if (JitOptions.enableICFramePointers) {
    fprintf(stderr,
            "PHOENIX_REPLAY: IC frame pointers are on (e.g. for perf), and "
            "replayed code assumes they're off\n");
    MOZ_CRASH("PHOENIX_REPLAY: IC frame pointers are on");
  }

  FILE* file = fopen(path, "r");
  if (!file) {
    fprintf(stderr, "PHOENIX_REPLAY: can't open %s\n", path);
    MOZ_CRASH("PHOENIX_REPLAY: can't open the replay table");
  }

  // Loading may happen during an OOM test: don't let it fail allocations.
  AutoEnterOOMUnsafeRegion oomUnsafe;
  Vector<char, 0, SystemAllocPolicy> line;
  size_t lineNumber = 0;
  int c = 0;
  while (c != EOF) {
    line.clear();
    while ((c = getc(file)) != EOF && c != '\n') {
      if (!line.append(char(c))) {
        oomUnsafe.crash("PHOENIX_REPLAY");
      }
    }
    lineNumber++;
    parseLine(path, lineNumber, line.begin(), line.length(), oomUnsafe);
  }
  fclose(file);
}

void ReplayTable::parseLine(const char* path, size_t lineNumber,
                            const char* line, size_t length,
                            AutoEnterOOMUnsafeRegion& oomUnsafe) {
  const char* fields[3];
  size_t lengths[3];
  size_t numFields = 0;
  size_t i = 0;
  while (true) {
    while (i < length &&
           (line[i] == ' ' || line[i] == '\t' || line[i] == '\r')) {
      i++;
    }
    if (i == length || (numFields == 0 && line[i] == '#')) {
      break;
    }
    if (numFields == 3) {
      ReplayError(path, lineNumber, "more than three fields");
    }
    size_t start = i;
    while (i < length && line[i] != ' ' && line[i] != '\t' && line[i] != '\r') {
      i++;
    }
    fields[numFields] = line + start;
    lengths[numFields] = i - start;
    numFields++;
  }
  if (numFields == 0) {
    return;
  }
  if (numFields != 3) {
    ReplayError(path, lineNumber,
                "expected a CacheKind, CacheIR bytes and code bytes");
  }

  ReplayEntry entry;
  bool foundKind = false;
  for (size_t k = 0; k < NumCacheKinds; k++) {
    const char* name = CacheKindNames[k];
    if (strlen(name) == lengths[0] &&
        memcmp(name, fields[0], lengths[0]) == 0) {
      entry.kind = CacheKind(k);
      foundKind = true;
    }
  }
  if (!foundKind) {
    ReplayError(path, lineNumber, "no such CacheKind");
  }
  if (!ParseHex(fields[1], lengths[1], entry.cacheIR, oomUnsafe) ||
      entry.cacheIR.empty()) {
    ReplayError(path, lineNumber, "the CacheIR bytes aren't hex");
  }
  if (!ParseHex(fields[2], lengths[2], entry.code, oomUnsafe) ||
      entry.code.empty()) {
    ReplayError(path, lineNumber, "the code bytes aren't hex");
  }
  if (find(entry.kind, entry.cacheIR.begin(), entry.cacheIR.length())) {
    ReplayError(path, lineNumber,
                "a second entry for the same kind and CacheIR");
  }
  if (!entries.append(std::move(entry))) {
    oomUnsafe.crash("PHOENIX_REPLAY");
  }
}

static const ReplayTable& GetReplayTable() {
  static ReplayTable table;
  return table;
}

bool phoenix::OnBaselineStubCompiled(JSContext* cx, CacheKind kind,
                                     const CacheIRWriter& writer,
                                     const char* stubName,
                                     BaselineCacheIRCompiler& comp,
                                     JitCode*& code) {
  const ReplayTable& table = GetReplayTable();
  size_t relocationBytes = comp.phoenixRelocationBytes();

  // Code with relocations has addresses the linker patches in, which bytes
  // alone can't carry.
  const ReplayEntry* entry = nullptr;
  const char* replay = nullptr;
  if (table.enabled) {
    entry = table.find(kind, writer.codeStart(), writer.codeLength());
    if (!entry) {
      replay = "notInTable";
    } else if (relocationBytes != 0) {
      replay = "refused";
      entry = nullptr;
    } else {
      replay = "substituted";
    }
  }

  if (ShouldRecord(kind)) {
    RecordCompile(kind, stubName, writer, comp.phoenixInfo(), relocationBytes,
                  comp.makesGCCalls(), code, replay);
  }
  if (!entry) {
    return true;
  }

  // Allocating the new code can GC, and nothing keeps the compiler's code
  // alive, so read what's needed from it first.
  uint8_t localTracingSlots = code->localTracingSlots();
  code = nullptr;

  TempAllocator temp(&cx->tempLifoAlloc());
  StackMacroAssembler masm(cx, temp);
  // On OOM, the assembler is marked as failed, and the linker fails too.
  (void)masm.appendRawCode(entry->code.begin(), entry->code.length());
  Linker linker(masm);
  JitCode* newCode = linker.newCode(cx, CodeKind::Baseline);
  if (!newCode) {
    cx->recoverFromOutOfMemory();
    return false;
  }
  newCode->setLocalTracingSlots(localTracingSlots);
  code = newCode;
  return true;
}
