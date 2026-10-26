"""Small stdio App Server client; uses installed Codex authentication unchanged."""
import json
import queue
import subprocess
import threading
import time


class AppServer:
    def __init__(self, binary, cwd, overrides=(), event=lambda x: None, *, command=None, request_handler=None):
        self.event = event
        self.messages = queue.Queue()
        self.pending = []
        self.seq = 0
        self.request_handler = request_handler
        args = list(command) if command is not None else [str(binary), 'app-server', '--listen', 'stdio://']
        for value in overrides:
            args += ['-c', value]
        self.process = subprocess.Popen(args, cwd=cwd, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Do not persist arbitrary stderr: it can include local configuration details.
        self.stderr = []
        def read():
            try:
                for line in self.process.stdout:
                    self.messages.put(json.loads(line))
            finally:
                self.messages.put(None)
        def errors():
            for line in self.process.stderr:
                self.stderr.append(line.decode('utf-8', errors='replace'))
                self.stderr = self.stderr[-20:]
        threading.Thread(target=read, daemon=True).start()
        threading.Thread(target=errors, daemon=True).start()
        try:
            self.info = self.call('initialize', {
                'clientInfo': {'name': 'goal_await_setup', 'version': '0.1'},
                'capabilities': {'experimentalApi': True}})
            self.send({'method': 'initialized'})
        except BaseException:
            self.close()
            raise

    def send(self, message):
        self.process.stdin.write((json.dumps(message) + '\n').encode())
        self.process.stdin.flush()

    def receive(self, deadline):
        value = self.messages.get(timeout=max(.001, deadline - time.monotonic()))
        if value is None:
            raise RuntimeError('App Server exited before completion')
        # Never approve arbitrary server-side requests automatically.
        if 'id' in value and 'method' in value:
            if self.request_handler is not None:
                try:
                    result = self.request_handler(value, self)
                except Exception:
                    self.send({'id': value['id'], 'error': {
                        'code': -32603, 'message': 'Setup handler failed'}})
                    raise
                self.send({'id': value['id'], 'result': result})
                return value
            self.send({'id': value['id'], 'error': {
                'code': -32601, 'message': 'Interactive request unsupported by setup client'}})
            raise RuntimeError('Interactive server request: ' + value['method'])
        self.event(value)
        return value

    def call(self, method, params, timeout=30):
        self.seq += 1
        ident = self.seq
        self.send({'id': ident, 'method': method, 'params': params})
        deadline = time.monotonic() + timeout
        while True:
            cached = next((i for i, v in enumerate(self.pending) if v.get('id') == ident
                           and 'method' not in v), None)
            value = self.pending.pop(cached) if cached is not None else self.receive(deadline)
            if value.get('id') == ident:
                if 'error' in value:
                    # Native config diagnostics can contain local values; keep
                    # them out of shareable setup output and handoff logs.
                    raise RuntimeError('Native RPC '+method+' failed; code='+str(value['error'].get('code')))
                return value['result']
            self.pending.append(value)

    def notification(self, method, timeout=180, thread_id=None):
        deadline = time.monotonic() + timeout
        def matches(value):
            return (value.get('method') == method and
                    (thread_id is None or value.get('params', {}).get('threadId') == thread_id))
        for i, value in enumerate(self.pending):
            if matches(value):
                return self.pending.pop(i)['params']
        while True:
            value = self.receive(deadline)
            if matches(value):
                return value['params']
            self.pending.append(value)

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=5)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
