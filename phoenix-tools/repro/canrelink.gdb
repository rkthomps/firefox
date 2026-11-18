set pagination off
set breakpoint pending on
break Assembler-x64.cpp:195 if rp.target == (void*)&JS::ToInt32
commands
  silent
  printf "from (src)       = %p\n", src
  printf "to   (rp.target) = %p\n", rp.target
  info symbol rp.target
  info symbol src
  p (long)((char*)rp.target - (char*)src)
  continue
end
run
