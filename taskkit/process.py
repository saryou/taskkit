import os
from datetime import tzinfo
from multiprocessing import Process, Event
from typing import Union

from .backend import Backend
from .event import EventBridge
from .scheduler import ScheduleEntry, Scheduler
from .signal import SignalCaptured, capture_signals, signal
from .task import TaskHandler
from .threads import WorkerThread, ServiceThread
from .services import RefreshWorkerLifetime, PurgeDeadWorkers, \
    RestoreAbandonedTasks, DiscardDisposableTasks
from .utils import logger


class TaskkitProcess(Process):
    def __init__(self,
                 num_worker_threads_per_group: dict[str, int],
                 backend: Backend,
                 bridge: EventBridge,
                 handler: TaskHandler,
                 schedule_entries: dict[str, list[ScheduleEntry]],
                 tzinfo: tzinfo,
                 polling_interval: Union[dict[str, float], float, None] = None,
                 helper_services: bool = True,
                 **kwargs):
        assert all(n >= 0 for n in num_worker_threads_per_group.values()), \
            'All values for num_worker_threads_per_group must be positive int.'
        self.num_worker_threads = num_worker_threads_per_group
        self.backend = backend
        self.bridge = bridge
        self.handler = handler
        self.schedule_entries = schedule_entries
        self.tzinfo = tzinfo

        if isinstance(polling_interval, (float, int)):
            polling_interval = {
                group: float(polling_interval)
                for group in num_worker_threads_per_group.keys()
            }
        elif polling_interval is None:
            polling_interval = dict()

        self.polling_interval = {
            group: polling_interval.get(group) or _make_polling_interval(n)
            for group, n in num_worker_threads_per_group.items()
        }

        self.helper_services = helper_services

        self._terminate_event = Event()
        super().__init__(**kwargs)

    def is_active(self) -> bool:
        return not self._terminate_event.is_set()

    def run(self):
        _id = f'{os.getpid()}'
        logger.info(f'[{_id}] taskkit process started: '
                    f'{self.num_worker_threads}')

        backend = self.backend
        workers: list[WorkerThread] = []
        for group, n in self.num_worker_threads.items():
            interval = self.polling_interval[group]
            for i in range(n):
                w = WorkerThread(group, backend, self.handler,
                                 polling_interval=interval,
                                 initial_delay=interval / n * i)
                w.start()
                workers.append(w)
        refresh_ttl = ServiceThread(
            RefreshWorkerLifetime(backend, workers))
        refresh_ttl.start()

        services = [
            *[ServiceThread(Scheduler(name, backend, entries, self.tzinfo))
              for name, entries in self.schedule_entries.items() if entries],
            *([
                ServiceThread(RestoreAbandonedTasks(backend)),
                ServiceThread(PurgeDeadWorkers(backend)),
                ServiceThread(DiscardDisposableTasks(backend)),
            ] if self.helper_services else []),
        ]
        for service in services:
            service.start()

        alive = {g for g, n in self.num_worker_threads.items() if n > 0}

        try:
            with capture_signals(signal.SIGTERM):
                while True:
                    try:
                        for event in self.bridge.receive_events():
                            target_groups =\
                                (alive
                                 if (_groups := event['groups']) is None
                                 else _groups)
                            target_workers = [w for w in workers
                                              if w.group in target_groups]

                            logger.info(f'[{_id}] control event received: {event}')

                            if event['name'] == 'shutdown':
                                alive.difference_update(target_groups)
                                for worker in target_workers:
                                    worker.should_stop = True
                                if not alive:
                                    raise SystemExit
                            elif event['name'] == 'pause':
                                for worker in target_workers:
                                    worker.paused = True
                            elif event['name'] == 'resume':
                                for worker in target_workers:
                                    worker.paused = False
                    except Exception:
                        pass
        except (KeyboardInterrupt, SystemExit, SignalCaptured):
            self._terminate_event.set()

        logger.info(f'[{_id}] shutting down...')

        for w in workers:
            w.should_stop = True
        for service in services:
            service.should_stop = True
        for service in services:
            service.join()
        for w in workers:
            w.join()
        refresh_ttl.should_stop = True
        refresh_ttl.join()


def _make_polling_interval(num_threads: int) -> float:
    return max(1, min(4, num_threads * 0.25))
