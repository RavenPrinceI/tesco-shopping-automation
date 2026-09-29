"""Bounded CDP transport and private run storage. No account data in logs."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import fcntl
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile

from urllib.parse import urlsplit
from uuid import uuid4
from typing import NoReturn

import tesco_amendment as t


def stop(state=t.State.OUTCOME_UNKNOWN) -> NoReturn:
    raise t.Halt(state)


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class Binding:
    browser: str
    target: str


class CDPTransport:
    def __init__(self, endpoint, target, *, timeout=8.0, execute=False):
        try:
            url = urlsplit(endpoint)
            if (url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port or
                    url.username or url.password or url.path or url.query or url.fragment or
                    not math.isfinite(timeout) or not 0 < timeout <= 60):
                stop()
        except (ValueError, TypeError):
            stop()
        self.endpoint, self.target, self.timeout = endpoint, target, timeout
        self.execute, self.ws_url = execute, None

    async def _http_exchange(self, path):
        if path not in ('/json/version', '/json/list'):
            stop()
        port = urlsplit(self.endpoint).port
        reader, writer = await asyncio.open_connection('127.0.0.1', port, limit=16384)
        try:
            writer.write(f'GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n'.encode())
            await writer.drain()
            header = (await reader.readuntil(b'\r\n\r\n')).decode('ascii')
            lines = header.split('\r\n')
            if lines[0].split()[1] != '200':
                stop()  # Includes redirects. Never leave the loopback endpoint.
            lengths = [int(line.split(':', 1)[1]) for line in lines if line.lower().startswith('content-length:')]
            if len(lengths) != 1 or not 0 <= lengths[0] <= 1024 * 1024:
                stop()
            return json.loads(await reader.readexactly(lengths[0]))
        finally:
            writer.close()

    def _json(self, path):
        async def bounded():
            return await asyncio.wait_for(self._http_exchange(path), self.timeout)
        try:
            return asyncio.run(bounded())
        except t.Halt:
            raise
        except Exception:
            stop()

    def _websocket(self, value):
        url = urlsplit(value)
        endpoint = urlsplit(self.endpoint)
        if (url.scheme != 'ws' or url.hostname != '127.0.0.1' or url.port != endpoint.port or
                url.username or url.password or url.query or url.fragment):
            stop()
        return url

    def identity(self, target):
        try:
            if target != self.target:
                stop(t.State.AMBIGUOUS_TARGET)
            version = self._json('/json/version')
            browser = self._websocket(version['webSocketDebuggerUrl'])
            if not re.fullmatch(r'/devtools/browser/[A-Za-z0-9-]+', browser.path):
                stop()
            targets = self._json('/json/list')
            matches = [p for p in targets if p.get('id') == target and p.get('type') == 'page']
            if len(matches) != 1:
                stop(t.State.AMBIGUOUS_TARGET)
            page = matches[0]
            url = urlsplit(page['url'])
            if url.scheme != 'https' or url.netloc != 'www.tesco.com' or not url.path.startswith('/groceries/'):
                stop(t.State.AUTH_REQUIRED)
            ws = self._websocket(page['webSocketDebuggerUrl'])
            if ws.path != '/devtools/page/' + target or not re.fullmatch(r'[A-Za-z0-9-]+', target):
                stop()
            self.ws_url = page['webSocketDebuggerUrl']
            return Binding(digest(browser.path), target)
        except t.Halt:
            raise
        except Exception:
            stop()

    async def _exchange(self, method, params):
        # Imported only on live use. Offline tests and --help need only stdlib.
        import websockets
        if self.ws_url is None:
            stop()
        async with websockets.connect(self.ws_url, proxy=None, open_timeout=self.timeout,
                                      close_timeout=0.2, max_size=1024 * 1024) as ws:
            await ws.send(json.dumps({'id': 1, 'method': method, 'params': params}))
            while True:
                reply = json.loads(await ws.recv())
                if reply.get('id') == 1:
                    if 'error' in reply:
                        stop()
                    return reply['result']

    def call(self, method, params):
        if method not in ('Runtime.evaluate', 'Input.dispatchMouseEvent') or (
                method.startswith('Input.') and not self.execute) or not self.ws_url:
            stop()
        async def bounded():
            return await asyncio.wait_for(self._exchange(method, params), self.timeout)
        try:
            return asyncio.run(bounded())
        except t.Halt:
            raise
        except Exception:
            stop()


def private_file(path, flags):
    fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        os.close(fd)
        stop()
    return fd


class RunStore:
    """One local owner; durable original context and a one-attempt execution fence."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            stop()
        self.lock = private_file(self.directory / 'lock', os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            os.close(self.lock)
            stop()
        self.state = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        os.close(self.lock)

    def _save(self):
        fd, name = tempfile.mkstemp(prefix='.state-', dir=self.directory)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(self.state, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.directory / 'state.json')
            directory_fd = os.open(self.directory, os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def prepare(self, binding, site_map, operations, discover):
        identity = digest([asdict(binding), site_map,
                           [(o.product, o.mode.value, o.quantity) for o in operations]])
        path = self.directory / 'state.json'
        if path.exists() or path.is_symlink():
            with os.fdopen(private_file(path, os.O_RDONLY)) as stream:
                self.state = json.load(stream)
            if self.state['identity'] != identity:
                stop(t.State.AMBIGUOUS_TARGET)
            raw = dict(self.state['context'])
            raw['requested_operations'] = tuple(t.Operation(o['product'], t.QuantityMode(o['mode']), o['quantity']) for o in raw['requested_operations'])
            raw['stage'] = t.State(raw['stage'])
            raw['submission_status'] = t.Submission(raw['submission_status'])
            context = t.AmendmentContext(**raw)
        else:
            # Existing journal without context means interrupted or lost storage.
            if (self.directory / 'journal.sqlite').exists():
                stop()
            context = discover()
            if (any(not re.fullmatch(r'[a-f0-9]{64}', v) for v in (context.order, context.slot)) or
                    any(not p.isdecimal() for p in context.baseline_quantities | context.expected_quantities)):
                stop()
            self.state = {'version': 1, 'identity': identity, 'run': str(uuid4()),
                          'binding': asdict(binding), 'context': asdict(context), 'attempted': False}
            self._save()
        return self.state['run'], context

    def claim_execution(self):
        if self.state is None or self.state['attempted']:
            stop()
        self.state['attempted'] = True
        self._save()

    def journal(self):
        path = self.directory / 'journal.sqlite'
        # Creation and verification before SQLite follows this path.
        fd = private_file(path, os.O_CREAT | os.O_RDWR)
        os.close(fd)
        return t.Journal(path)
