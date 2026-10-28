"""Run debug code and task functions on the task executor's thread.

Shared by the Run Code and Debug tabs and by agent tools. Work goes through
``TaskExecutor.submit()``, so it gets the same frame, pause and cancel handling
as a real task and never runs on the GUI thread. ``print()`` output and ``ok``
log records are captured from the executor thread only, so other threads keep
writing to the console as usual.
"""

import ast
import builtins
import concurrent.futures
import inspect
import itertools
import linecache
import logging
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from ok.task.exceptions import TaskDisabledException

_log_formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
_capture_lock = threading.Lock()
_run_ids = itertools.count(1)


@dataclass
class RunResult:
    output: str = ''
    value: str | None = None
    error: str | None = None
    cancelled: bool = False
    duration: float = 0.0
    raw: Any = field(default=None, repr=False)

    @property
    def ok(self):
        return self.error is None and not self.cancelled


class _ThreadRoutingStream:
    """Stands in for sys.stdout/sys.stderr and sends captured threads' writes to their sinks."""

    def __init__(self, original):
        self.original = original
        self.sinks = {}
        self._write_lock = threading.RLock()

    def write(self, text):
        sink = self.sinks.get(threading.get_ident())
        if sink is not None:
            sink(text)
            return len(text)
        if self.original is None:
            return len(text)
        with self._write_lock:
            return self.original.write(text)

    def flush(self):
        if self.original is not None:
            with self._write_lock:
                self.original.flush()

    def __getattr__(self, name):
        return getattr(self.original, name)


class _ThreadLogHandler(logging.Handler):
    def __init__(self, thread_id, sink):
        super().__init__(logging.DEBUG)
        self.thread_id = thread_id
        self.sink = sink
        self.setFormatter(_log_formatter)

    def emit(self, record):
        if record.thread != self.thread_id:
            return
        try:
            self.sink(self.format(record) + '\n')
        except Exception:
            self.handleError(record)


def _install_output_routing():
    """Keep stream identities stable while jobs and their child threads run.

    Installing/removing proxies on the executor thread used to race with a
    child's print() and the host's output capture. A router stays installed;
    only its thread-local destinations change between jobs. Hosts may replace
    sys streams (for example, a new pytest capture), so rebind at submission.
    """
    with _capture_lock:
        streams = []
        for name in ('stdout', 'stderr'):
            stream = getattr(sys, name)
            if not isinstance(stream, _ThreadRoutingStream):
                stream = _ThreadRoutingStream(stream)
                setattr(sys, name, stream)
            streams.append(stream)
        return streams


@contextmanager
def capture_output(sink):
    """Send the calling thread's stdout/stderr and ``ok`` logs to ``sink``."""
    thread_id = threading.get_ident()
    streams = _install_output_routing()
    with _capture_lock:
        previous = [stream.sinks.get(thread_id) for stream in streams]
        for stream in streams:
            stream.sinks[thread_id] = sink
    handler = _ThreadLogHandler(thread_id, sink)
    ok_logger = logging.getLogger('ok')
    ok_logger.addHandler(handler)
    try:
        yield
    finally:
        ok_logger.removeHandler(handler)
        with _capture_lock:
            for stream, old_sink in zip(streams, previous):
                if old_sink is None:
                    stream.sinks.pop(thread_id, None)
                else:
                    stream.sinks[thread_id] = old_sink


def _safe_repr(value):
    try:
        return repr(value)
    except Exception as e:
        return f'<{type(value).__name__} repr failed: {e}>'


def submit_call(executor, fn, name=None, owner=None, capture=None, interaction=None, on_output=None):
    """Submit ``fn`` to the executor with output capture.

    The returned job's future resolves to a RunResult; exceptions from ``fn``
    become ``RunResult.error`` instead of failing the future. ``on_output`` is
    called on the executor thread with each chunk of output as it is written.
    """

    _install_output_routing()

    def run():
        chunks = []

        def sink(text):
            chunks.append(text)
            if on_output is not None:
                try:
                    on_output(text)
                except Exception:
                    pass

        result = RunResult()
        start = time.time()
        with capture_output(sink):
            try:
                value = fn()
                result.raw = value
                if value is not None:
                    result.value = _safe_repr(value)
            except TaskDisabledException:
                result.cancelled = True
            except BaseException:
                # Includes SystemExit from exit() in user code.
                result.error = traceback.format_exc()
        result.output = ''.join(chunks)
        result.duration = time.time() - start
        return result

    return executor.submit(run, name=name, owner=owner, capture=capture, interaction=interaction)


def wait_result(job, timeout=None):
    """Wait for a job from submit_call().

    On timeout the job is cancelled; it stops at its next sleep, frame request
    or wait, not immediately.
    """
    try:
        return job.future.result(timeout)
    except concurrent.futures.TimeoutError:
        job.cancel()
        return RunResult(error=f'Timed out after {timeout}s', cancelled=True)
    except concurrent.futures.CancelledError:
        return RunResult(cancelled=True)


def _execute(code, namespace):
    """exec() ``code`` and return the value of its last line if that line is an expression."""
    filename = f'<run_code-{next(_run_ids)}>'
    # Lets tracebacks show the failing source line.
    linecache.cache[filename] = (len(code), None, code.splitlines(True), filename)
    tree = ast.parse(code, filename, 'exec')
    last = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = ast.Expression(tree.body.pop().value)
    exec(compile(tree, filename, 'exec'), namespace)
    if last is not None:
        return eval(compile(last, filename, 'eval'), namespace)
    return None


class CodeSession:
    """A namespace that persists across runs, like a REPL.

    The same dict serves as globals and locals, so functions defined in one run
    can see names defined beside them and in earlier runs.
    """

    def __init__(self, executor, owner=None, extra=None):
        _install_output_routing()
        self.executor = executor
        self.owner = owner
        self._extra = dict(extra or {})
        self.namespace = None
        self.reset()

    def reset(self):
        from ok import og
        self.namespace = {
            '__name__': '__run_code__',
            '__builtins__': builtins,
            'og': og,
            'executor': self.executor,
            **self._extra,
        }

    def submit(self, code, task=None, capture=None, interaction=None, on_output=None):
        """Submit ``code`` and return its job; ``task`` is bound to the name ``task``."""
        namespace = self.namespace

        def run():
            if task is not None:
                namespace['task'] = task
            return _execute(code, namespace)

        return submit_call(self.executor, run, name='run_code', owner=self.owner, capture=capture,
                           interaction=interaction, on_output=on_output)

    def run(self, code, task=None, timeout=None, on_output=None):
        return wait_result(self.submit(code, task=task, on_output=on_output), timeout)


def submit_task_call(executor, task, attr_name, args=(), kwargs=None, images=None, owner=None, on_output=None):
    """Call a task's method, or read its attribute, on the executor thread.

    With ``images`` the call sees those screenshots instead of the live capture,
    and its input actions do nothing. Raises AttributeError if the task has no
    such attribute, and CaptureException if an image cannot be read.
    """
    try:
        # getattr_static: a property such as ``frame`` must not run here, on the caller's thread.
        inspect.getattr_static(task, attr_name)
    except AttributeError:
        raise AttributeError(f'{type(task).__name__} has no attribute {attr_name!r}') from None
    capture = interaction = None
    if images:
        from ok.device.capture_methods.image import ImageCaptureMethod
        from ok.device.interaction_methods.do_nothing import DoNothingInteraction
        capture = ImageCaptureMethod(executor.exit_event, list(images))
        interaction = DoNothingInteraction(capture)

    def call():
        attr = getattr(task, attr_name)
        if callable(attr):
            return attr(*args, **(kwargs or {}))
        if args or kwargs:
            raise TypeError(f'{attr_name} is not callable')
        return attr

    return submit_call(executor, call, name=f'{type(task).__name__}.{attr_name}', owner=owner,
                       capture=capture, interaction=interaction, on_output=on_output)
