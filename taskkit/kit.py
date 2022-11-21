import time
from datetime import datetime, tzinfo
from typing import Any, Callable

from .backend import Backend
from .controller import Controller, Shutdown, Pause, Resume
from .process import TaskkitProcess
from .result import Result
from .scheduler import ScheduleEntry
from .signal import SignalCaptured, capture_signals, signal
from .task import Task, TaskHandler, DEFAULT_TASK_TTL
from .utils import local_tz
from .worker import EagerWorker


class Kit:
    def __init__(self,
                 backend: Backend,
                 controller: Controller,
                 handler: TaskHandler):
        self.backend = backend
        self.controller = controller
        self.handler = handler
        self.eager_worker = EagerWorker(backend, handler)

    def start(self,
              num_processes: int,
              num_worker_threads_per_group: dict[str, int],
              schedule_entries: dict[str, list[ScheduleEntry]] = {},
              tzinfo: tzinfo | None = None,
              should_restart: Callable[[TaskkitProcess], bool] = lambda _: False):

        def _start():
            return self.start_process(
                num_worker_threads_per_group=num_worker_threads_per_group,
                schedule_entries=schedule_entries,
                tzinfo=tzinfo,
                daemon=True)

        processes = [_start() for _ in range(num_processes)]

        try:
            with capture_signals(signal.SIGTERM):
                while True:
                    for i, p in enumerate(list(processes)):
                        if not p.is_alive() or should_restart(p):
                            if p.is_active():
                                p.terminate()
                            p.join()
                            processes[i] = _start()
                    time.sleep(1)
        except (KeyboardInterrupt, SystemExit, SignalCaptured) as e:
            for p in processes:
                if p.is_active():
                    p.terminate()
            for p in processes:
                p.join()
            if isinstance(e, SignalCaptured):
                raise SystemExit from e
            else:
                raise

    def start_processes(self,
                        num_processes: int,
                        num_worker_threads_per_group: dict[str, int],
                        schedule_entries: dict[str, list[ScheduleEntry]] = {},
                        tzinfo: tzinfo | None = None,
                        daemon: bool = True) -> list[TaskkitProcess]:
        return [
            self.start_process(num_worker_threads_per_group,
                               schedule_entries,
                               tzinfo,
                               daemon)
            for _ in range(num_processes)
        ]

    def start_process(self,
                      num_worker_threads_per_group: dict[str, int],
                      schedule_entries: dict[str, list[ScheduleEntry]] = {},
                      tzinfo: tzinfo | None = None,
                      daemon: bool = True) -> TaskkitProcess:
        p = TaskkitProcess(
            num_worker_threads_per_group=num_worker_threads_per_group,
            backend=self.backend,
            controller=self.controller,
            handler=self.handler,
            schedule_entries=schedule_entries,
            tzinfo=tzinfo or local_tz(),
            daemon=daemon)
        p.start()
        return p

    def initiate_task(self,
                      group: str,
                      name: str,
                      data: Any,
                      due: datetime | None = None,
                      ttl: float = DEFAULT_TASK_TTL,
                      eager: bool = False) -> Result[Any]:
        encoded = self.handler.encode_data(group, name, data)
        task = Task.init(group, name=name, data=encoded, due=due, ttl=ttl)
        if eager:
            return self.eager_worker.handle_task(task)
        self.backend.put_tasks(task)
        return Result(self.backend, self.handler, task.id)

    def send_shutdown_event(self, groups: set[str] | None = None):
        event: Shutdown = {
            'name': 'shutdown',
            'groups': None if groups is None else list(groups),
        }
        self.controller.send_event(event)

    def send_pause_event(self, groups: set[str] | None = None):
        event: Pause = {
            'name': 'pause',
            'groups': None if groups is None else list(groups),
        }
        self.controller.send_event(event)

    def send_resume_event(self, groups: set[str] | None = None):
        event: Resume = {
            'name': 'resume',
            'groups': None if groups is None else list(groups),
        }
        self.controller.send_event(event)
