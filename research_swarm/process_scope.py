"""Bound the lifetime of research processes, including detached descendants.

Windows uses a kill-on-close Job Object. A trusted bootstrap waits on stdin until
it has joined the job, so user code cannot race assignment. This is lifetime
management, not a filesystem/network security sandbox.
https://learn.microsoft.com/windows/win32/procthread/job-objects
"""
import json
import os
import signal
import subprocess
import sys


class ProcessScope:
    def __init__(self, argv, *, cwd, env, stdout, stderr):
        self.process = None
        self.job = None
        if os.name == 'nt':
            self._create_job()
        try:
            if self.job:
                bootstrap = ('import json,subprocess,sys; '
                             'argv=json.loads(sys.stdin.buffer.readline()); '
                             'sys.exit(subprocess.Popen(argv,stdin=subprocess.DEVNULL).wait())')
                self.process = subprocess.Popen([sys.executable, '-I', '-u', '-c', bootstrap],
                    cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                if not self.kernel.AssignProcessToJobObject(self.job, int(self.process._handle)):
                    import ctypes
                    raise ctypes.WinError()
                self.process.stdin.write(json.dumps(argv).encode('utf-8') + b'\n')
                self.process.stdin.close()
            else:
                self.process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                    stdout=stdout, stderr=stderr, start_new_session=True)
        except BaseException:
            if self.process and self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=15)
            self.close()
            raise

    def _create_job(self):
        import ctypes
        from ctypes import wintypes as wt
        class BasicLimits(ctypes.Structure):
            _fields_ = [('processTime', ctypes.c_int64), ('jobTime', ctypes.c_int64),
                        ('flags', wt.DWORD), ('minimumWorkingSet', ctypes.c_size_t),
                        ('maximumWorkingSet', ctypes.c_size_t), ('activeLimit', wt.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wt.DWORD), ('scheduling', wt.DWORD)]
        class IOCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ('readOps', 'writeOps', 'otherOps', 'readBytes', 'writeBytes', 'otherBytes')]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('basic', BasicLimits), ('io', IOCounters),
                        ('processMemory', ctypes.c_size_t), ('jobMemory', ctypes.c_size_t),
                        ('peakProcessMemory', ctypes.c_size_t), ('peakJobMemory', ctypes.c_size_t)]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
        kernel.CreateJobObjectW.restype = wt.HANDLE
        kernel.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        kernel.SetInformationJobObject.restype = wt.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
        kernel.AssignProcessToJobObject.restype = wt.BOOL
        kernel.CloseHandle.argtypes = [wt.HANDLE]
        kernel.CloseHandle.restype = wt.BOOL
        self.kernel = kernel
        self.job = kernel.CreateJobObjectW(None, None)
        if not self.job:
            raise ctypes.WinError()
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError()
            self.close()
            raise error

    def close(self):
        if self.job:
            self.kernel.CloseHandle(self.job)
            self.job = None
        elif os.name != 'nt' and self.process:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.process and self.process.poll() is None:
            self.process.wait(timeout=15)
