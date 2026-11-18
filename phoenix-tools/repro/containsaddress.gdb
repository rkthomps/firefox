set pagination off
set breakpoint pending on
break Assembler-x64.cpp:134 if target.value == (void*)&JS::ToInt32
commands
  silent
  printf "p     = %p\n", target.value
  printf "base_ = %p\n", 'ProcessExecutableMemory.cpp'::execMemory.base_
  info symbol target.value
  info symbol 'ProcessExecutableMemory.cpp'::execMemory.base_
  continue
end
run
