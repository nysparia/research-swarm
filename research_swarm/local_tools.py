"""Research process tools, without a second agent or access to provider credentials.

Processes use a task-local venv, working directory, bounded lifetime and durable
receipts. This is process isolation, not an OS security sandbox.
"""
from __future__ import annotations

import hashlib
import csv
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from .process_scope import ProcessScope


PACKAGES = {'numpy', 'scipy', 'scikit-learn', 'pandas', 'matplotlib', 'onnx',
            'onnxruntime', 'skl2onnx', 'psutil', 'torch', 'torchvision', 'pillow'}
ENVIRONMENT_CODE = '''import json,platform,os,importlib.metadata,ctypes
data={"os":platform.platform(),"architecture":platform.machine(),"python":platform.python_version(),"cpu":platform.processor(),"logicalCpus":os.cpu_count(),"packages":{}}
if os.name=="nt":
 import winreg
 try:
  with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r"HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0") as key:data["cpu"]=winreg.QueryValueEx(key,"ProcessorNameString")[0].strip()
 except OSError:pass
 class Memory(ctypes.Structure):
  _fields_=[("length",ctypes.c_ulong),("load",ctypes.c_ulong)]+[(n,ctypes.c_ulonglong) for n in ("totalPhysical","availablePhysical","totalPage","availablePage","totalVirtual","availableVirtual","extended")]
 m=Memory();m.length=ctypes.sizeof(m)
 if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):data["physicalMemoryBytes"]=m.totalPhysical
elif platform.system()=="Linux":
 try:
  pages,page_size=os.sysconf("SC_PHYS_PAGES"),os.sysconf("SC_PAGE_SIZE")
  if pages>0 and page_size>0:data["physicalMemoryBytes"]=pages*page_size
 except (AttributeError,OSError,ValueError):pass
 try:
  with open("/proc/cpuinfo",encoding="utf-8",errors="replace") as info:
   for line in info:
    name,separator,value=line.partition(":")
    if separator and name.strip().lower() in ("model name","hardware") and value.strip():
     data["cpu"]=value.strip();break
 except OSError:pass
for name in ("numpy","scipy","scikit-learn","onnx","onnxruntime","skl2onnx","torch","psutil"):
 try:data["packages"][name]=importlib.metadata.version(name)
 except importlib.metadata.PackageNotFoundError:data["packages"][name]=None
print(json.dumps(data,ensure_ascii=False))
'''


class LocalResearchTools:
    names = ('local_environment', 'python_install', 'python_run', 'artifact_read')

    def __init__(self, artifact_root: Path, python_executable=None):
        # Keep the caller's absolute spelling (including Windows 8.3 paths)
        # for task-local environment variables and relative artifact records.
        # Security checks resolve paths separately before comparing boundaries.
        self.root = Path(artifact_root).absolute()
        self.root.mkdir(parents=True, exist_ok=True)
        self.python = Path(python_executable) if python_executable else None
        self._environment_lock = threading.Lock()
        self._process_lock = threading.Lock()

    def _env(self, directory):
        home, temporary = self.root/'tool-home', self.root/'temporary'
        home.mkdir(parents=True, exist_ok=True)
        temporary.mkdir(parents=True, exist_ok=True)
        plotting = home/'matplotlib'
        plotting.mkdir(parents=True, exist_ok=True)
        isolated = self.root/'python-env'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        interpreter = self.python or (isolated if isolated.is_file() else Path(sys.executable))
        paths = [str(interpreter.parent)]
        if os.name == 'nt':
            env = {key: value for key, value in os.environ.items()
                   if key.upper() in {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE'}}
            system = Path(os.environ.get('SYSTEMROOT', '/'))
            paths.extend((str(system/'System32'), str(system)))
        else:
            env = {}
            # Standard interpreter and OS directories only; never reuse parent PATH.
            paths.extend(path for path in os.defpath.split(os.pathsep) if path and Path(path).is_absolute())
        env.update(PATH=os.pathsep.join(dict.fromkeys(paths)),
                   PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1',
                   PYTHONNOUSERSITE='1', PIP_DISABLE_PIP_VERSION_CHECK='1', PIP_CONFIG_FILE=os.devnull,
                   PIP_CACHE_DIR=str(self.root/'pip-cache'), UV_HTTP_TIMEOUT='30', UV_HTTP_RETRIES='1',
                   OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2', MKL_NUM_THREADS='2',
                   HOME=str(home), USERPROFILE=str(home), TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary),
                   MPLBACKEND='Agg', MPLCONFIGDIR=str(plotting))
        return env

    def _ensure_python(self, log, cancelled):
        if self.python:
            return self.python
        with self._environment_lock:
            directory = self.root/'python-env'
            executable = directory/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
            if not executable.is_file():
                log('正在创建本课题独立 Python 环境，不修改系统 Python。')
                # Bundled setuptools includes deeply nested test data that breaks Windows MAX_PATH.
                # Stdlib experiments need only the isolated interpreter; install pip lazily if uv is absent.
                result = self._process([sys.executable, '-m', 'venv', '--without-pip', str(directory)],
                                       self.root, self.root/'environment-setup', 90, log, cancelled)
                if result['status'] != 'completed':
                    raise RuntimeError('课题 Python 环境创建失败：'+result['stderr'][-1800:])
            return executable

    def _process(self, argv, cwd, run, timeout, log, cancelled):
        run.mkdir(parents=True, exist_ok=True)
        started=time.monotonic()
        status='completed'
        out_path, err_path=run/'stdout.txt', run/'stderr.txt'
        with out_path.open('wb') as stdout, err_path.open('wb') as stderr:
            scope=ProcessScope(argv, cwd=cwd, env=self._env(cwd), stdout=stdout, stderr=stderr)
            process=scope.process
            last_progress=started
            offsets = {out_path: 0, err_path: 0}
            try:
                while process.poll() is None:
                    if cancelled(): status='cancelled'; break
                    if time.monotonic()-started>timeout: status='timed_out'; break
                    if out_path.stat().st_size+err_path.stat().st_size>4*1024*1024:
                        status='output_limit'; break
                    if time.monotonic()-last_progress>=10:
                        log(f'本机进程运行中 · 已用 {int(time.monotonic()-started)} 秒 · 日志已保存')
                        for path, offset in offsets.items():
                            size = path.stat().st_size
                            if size > offset:
                                with path.open('rb') as file:
                                    file.seek(max(offset, size-1800))
                                    log(file.read(1800).decode('utf-8', errors='replace'))
                                offsets[path] = size
                        last_progress=time.monotonic()
                    time.sleep(.1)
            finally:
                scope.close()
        if process.returncode and status=='completed': status='failed'
        return {'status':status,'returnCode':process.returncode,'elapsedMs':round((time.monotonic()-started)*1000),
                'stdout':out_path.read_text('utf-8',errors='replace')[-30000:],
                'stderr':err_path.read_text('utf-8',errors='replace')[-12000:]}

    def call(self, name, arguments, node, context, log):
        activity = context.get('local_activity', lambda active: None)
        activity(True)
        try:
            result = self._call(name, arguments, node, context, log)
            if result.get('evidence') and context.get('record_execution'):
                context['record_execution'](result)
            if name == 'artifact_read' and context.get('record_artifact_read'):
                context['record_artifact_read'](result)
            return result
        finally:
            activity(False)

    def _call(self, name, arguments, node, context, log):
        if name not in self.names or not isinstance(arguments,dict):
            raise ValueError('本机科研工具或参数无效')
        if name=='artifact_read':
            requested = self.root / str(arguments.get('path', ''))
            path = requested.resolve()
            resolved_root = self.root.resolve()
            if not path.is_relative_to(resolved_root/'runs') or not path.is_file() or path.is_symlink():
                raise ValueError('只能读取本课题 runs 内的实验产物')
            large = path.stat().st_size > 256*1024
            if large and (not arguments.get('preview') or path.suffix not in ('.csv', '.json') or path.stat().st_size > 4*1024*1024):
                raise ValueError('文本产物超过 256 KiB；CSV/JSON 可用 preview=true 读取校验摘要，最多 4 MiB')
            content = path.read_bytes()
            text = content.decode('utf-8-sig')
            result = {'path':requested.relative_to(self.root).as_posix(),'text':text, 'sha256':hashlib.sha256(content).hexdigest(), 'bytes':len(content)}
            if large and path.suffix == '.csv':
                reader = csv.DictReader(io.StringIO(text)); rows = list(reader)
                result.update(preview=True, rowCount=len(rows), columns=reader.fieldnames,
                              text='\n'.join(text.splitlines()[:21]) + '\n[仅首20条观察；全文哈希及行数已核验]')
            elif large:
                data = json.loads(text)
                remaining = [300]
                def summarize(value, depth=0):
                    remaining[0] -= 1
                    if remaining[0] <= 0 or depth >= 5:
                        return {'previewType': type(value).__name__, 'omitted': True}
                    if isinstance(value, list):
                        return {'previewType': 'array', 'length': len(value), 'firstItems': [summarize(v, depth+1) for v in value[:10]]}
                    if isinstance(value, dict):
                        fields = dict(list(value.items())[:30])
                        summary = {k: summarize(v, depth+1) for k, v in fields.items()}
                        if len(fields) < len(value): summary['omittedKeys'] = len(value) - len(fields)
                        return summary
                    if isinstance(value, str) and len(value) > 1000:
                        return {'previewType': 'string', 'length': len(value), 'prefix': value[:1000]}
                    return value
                result.update(preview=True, fields=list(data)[:100] if isinstance(data, dict) else None,
                              keyCount=len(data) if isinstance(data, dict) else None,
                              text=json.dumps(summarize(data), ensure_ascii=False, indent=2)[:48000] + '\n[仅预览；全文哈希及字节数已核验，数组显示长度与前10项]')
            return result
        packages=[]
        if name=='python_install':
            indexes = {'pypi':'https://pypi.org/simple', 'tuna':'https://pypi.tuna.tsinghua.edu.cn/simple'}
            source = arguments.get('source', 'pypi')
            if source not in indexes: raise ValueError('安装来源只支持 pypi 或 tuna，不接受任意镜像地址')
            index = indexes[source]
            packages=arguments.get('packages',[])
            if not isinstance(packages,list) or not 1<=len(packages)<=12:
                raise ValueError('需要 1–12 个已支持的科研依赖包名')
            for package in packages:
                if not isinstance(package,str) or not re.fullmatch(r'[a-z][a-z0-9-]*(?:==[0-9][a-zA-Z0-9.+-]*)?',package) or package.split('==')[0] not in PACKAGES:
                    raise ValueError('只允许从 PyPI 安装已支持的科研库，不接受 URL、命令选项或任意包')
        code=ENVIRONMENT_CODE if name=='local_environment' else arguments.get('code','')
        if name=='python_run' and (not isinstance(code,str) or not code.strip() or len(code)>60000):
            raise ValueError('实验代码须为 1–60000 字符 Python 脚本')
        cancelled=context.get('cancelled',lambda:False)
        if cancelled(): raise RuntimeError('节点已暂停，未启动本机执行')
        with self._process_lock:
            if cancelled(): raise RuntimeError('节点已暂停，未启动本机执行')
            run=self.root/'runs'/('local-'+uuid.uuid4().hex[:16]);run.mkdir(parents=True)
            node_name=hashlib.sha256(str(node['id']).encode()).hexdigest()[:12]
            cwd=self.root/'laboratory'/f'round-{int(context.get("round",1))}'/node_name
            cwd.mkdir(parents=True,exist_ok=True)
            started=datetime.now(timezone.utc).isoformat()
            python=self._ensure_python(log,cancelled)
            script=run/'experiment.py'
            if name=='python_install':
                uv = shutil.which('uv')
                if uv:
                    argv=[uv,'--no-config','--no-progress','--no-python-downloads',
                          '--cache-dir',str(self.root/'package-cache'),'pip','install','--python',str(python),
                          '--default-index',index,'--only-binary',':all:',*packages]
                else:
                    bootstrap = "import ensurepip,pathlib,runpy,sys; wheel=next((pathlib.Path(ensurepip.__file__).parent/'_bundled').glob('pip-*.whl')); sys.path.insert(0,str(wheel)); sys.argv=['pip','--isolated','install','--no-index','--no-deps','--upgrade',str(wheel)]; runpy.run_module('pip',run_name='__main__')"
                    log('正在从 Python 自带安装包准备课题 pip，不安装无关的环境测试文件。')
                    prepared = self._process([str(python), '-I', '-X', 'utf8', '-c', bootstrap], self.root,
                                             self.root/'environment-pip', 90, log, cancelled)
                    if prepared['status'] != 'completed':
                        raise RuntimeError('课题 pip 初始化失败：' + prepared['stderr'][-1800:])
                    argv=[str(python),'-m','pip','--isolated','install','--only-binary',':all:',
                          '--timeout','30','--retries','1','--index-url',index,*packages]
                timeout=300
            else:
                script.write_text(code,encoding='utf-8',newline='\n')
                # -I ignores PYTHONUTF8/PYTHONIOENCODING; make UTF-8 explicit for Chinese process logs.
                argv=[str(python),'-I','-X','utf8','-u',str(script)]
                timeout=max(1,min(180,int(arguments.get('timeoutSeconds',90))))
            log('本机执行：'+('安装科研依赖 '+', '.join(packages)+' · 来源 '+source if packages else name)+' · 工作目录 '+cwd.relative_to(self.root).as_posix())
            before = {path: (path.stat().st_size, path.stat().st_mtime_ns)
                      for path in cwd.rglob('*') if path.is_file() and not path.is_symlink()}
            script_digest = hashlib.sha256(script.read_bytes()).hexdigest() if script.is_file() else None
            if context.get('record_execution_started'):
                context['record_execution_started']({'tool': name, 'status': 'running', 'nodeId': node['id'],
                    'nodeVersion': node.get('version', 1), 'round': context.get('round', 1), 'createdAt': started,
                    'workingDirectory': cwd.relative_to(self.root).as_posix(),
                    'script': script.relative_to(self.root).as_posix() if script.is_file() else None,
                    'scriptSha256': script_digest,
                    'stdoutPath': (run / 'stdout.txt').relative_to(self.root).as_posix(),
                    'stderrPath': (run / 'stderr.txt').relative_to(self.root).as_posix(),
                    'dashboardBefore': before.get(cwd / 'research-dashboard.json')})
            result=self._process(argv,cwd,run,timeout,log,cancelled)
            artifacts=[]
            for path in sorted(cwd.rglob('*')):
                if result['status']=='cancelled': break  # Finish cancellation receipt promptly.
                relative_parts = path.relative_to(cwd).parts
                if relative_parts[:2] == ('pip', 'cache') or any(part in ('.cache', '__pycache__') or part.startswith('pip-') for part in relative_parts):
                    continue
                if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(cwd) or '__pycache__' in path.parts:
                    continue
                if before.get(path) == (path.stat().st_size, path.stat().st_mtime_ns): continue
                if path.stat().st_size>50*1024*1024 or len(artifacts)>=30: continue
                target=run/'artifacts'/path.relative_to(cwd);target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(path,target)
                artifacts.append({'name':path.name,'path':target.relative_to(self.root).as_posix(),
                                  'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'bytes':target.stat().st_size})
            result.update(tool=name,nodeId=node['id'],nodeVersion=node.get('version',1),
                          executionToken=context.get('executionToken'),round=context.get('round',1),createdAt=started,
                          command=argv,workingDirectory=cwd.relative_to(self.root).as_posix(),artifacts=artifacts,
                          stdoutPath=(run/'stdout.txt').relative_to(self.root).as_posix(),
                          stderrPath=(run/'stderr.txt').relative_to(self.root).as_posix())
            if script_digest:
                result['script']=script.relative_to(self.root).as_posix()
                result['scriptSha256']=script_digest
                result['scriptChanged']=not script.is_file() or hashlib.sha256(script.read_bytes()).hexdigest() != script_digest
            receipt=run/'receipt.json'
            temporary=run/'receipt.pending'
            temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(receipt)
            digest=hashlib.sha256(receipt.read_bytes()).hexdigest()
            quote=json.dumps({key:result[key] for key in ('tool','status','returnCode','elapsedMs','stdout','stderr','artifacts')},ensure_ascii=False)
            evidence={'id':'experiment:local:'+digest[:20],'paperId':'','type':'experiment','extractor':'local_process',
                      'locator':receipt.relative_to(self.root).as_posix(),'quote':quote[:45000],
                      'sha256':digest,'tool':name,'executionStatus':result['status'],'confidence':1.0}
            result['evidence']=[evidence]
            log(f'本机执行结束：{name} · {result["status"]} · 退出码 {result["returnCode"]} · {result["elapsedMs"]} ms · 凭据 {evidence["id"]}')
            if result['stderr']: log(result['stderr'][-1800:])
            if result['stdout']: log(result['stdout'][-2200:])
            return result
