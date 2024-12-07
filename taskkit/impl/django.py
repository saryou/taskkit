import sys
import time
from typing import Generator, Optional

from django.db.transaction import atomic, Atomic
from django.db.utils import OperationalError, IntegrityError

from ..backend import Backend, Lock, NotFound, NoResult, Failed
from ..contrib.django.models import TaskkitControlEvent, TaskkitLock, TaskkitWorker, TaskkitTask, TaskkitTaskQueue, TaskkitSchedulerState
from ..event import EventBridge, ControlEvent, encode_control_event, decode_control_event
from ..kit import Kit
from ..stage import StageInfo
from ..task import Task, TaskHandler
from ..utils import cur_ts


class DjangoEventBridge(EventBridge):
    def receive_events(self) -> Generator[ControlEvent, None, None]:
        offset = cur_ts()
        while True:
            for event in TaskkitControlEvent.objects\
                    .filter(sent__gt=offset)\
                    .order_by('sent'):
                offset = event.sent
                if (e := decode_control_event(event.data)):
                    yield e
            time.sleep(1)

    def send_event(self, event: ControlEvent):
        TaskkitControlEvent.objects.create(
            data=encode_control_event(event),
            sent=cur_ts())


class DjangoLock(Lock):
    def __init__(self, target: str):
        self.target = target
        self.acquired = False
        self.ensured = False
        self.atomic: Optional[Atomic] = None

    def acquire(self) -> bool:
        if self.acquired:
            return True

        if not self.ensured:
            self.ensured = True
            try:
                with atomic():
                    TaskkitLock.objects\
                        .select_for_update(skip_locked=True)\
                        .get(pk=self.target)
            except TaskkitLock.DoesNotExist:
                try:
                    TaskkitLock.objects.create(pk=self.target)
                except IntegrityError:
                    pass
            except Exception:
                pass

        self.atomic = atomic()
        self.atomic.__enter__()
        try:
            TaskkitLock.objects\
                .select_for_update(skip_locked=True)\
                .get(pk=self.target)
            self.acquired = True
            return True
        except Exception:
            self.atomic.__exit__(*sys.exc_info())
            self.atomic = None
            return False

    def release(self):
        if self.atomic:
            self.atomic.__exit__(*sys.exc_info())
            self.atomic = None
            self.acquired = False


class DjangoBackend(Backend):
    def __init__(self, use_queue_table: bool):
        self.use_queue_table = use_queue_table

    def set_worker_ttl(self, worker_ids: set[str], expires_at: float):
        if not worker_ids:
            return
        workers = [
            TaskkitWorker(pk=wid, expires=expires_at)
            for wid in worker_ids
        ]
        TaskkitWorker.objects.bulk_create(workers, ignore_conflicts=True)
        TaskkitWorker.objects.bulk_update(workers, ['expires'])

    def get_workers(self) -> list[tuple[str, float]]:
        return [
            (w.pk, w.expires)
            for w in TaskkitWorker.objects.order_by('expires')
        ]

    def purge_workers(self, worker_ids: set[str]):
        if not worker_ids:
            return
        TaskkitWorker.objects.filter(pk__in=worker_ids).delete()

    def put_tasks(self, *tasks: Task):
        if not tasks:
            return
        objects = [self._task_to_db(t) for t in tasks]
        with atomic():
            TaskkitTask.objects.bulk_create(objects, ignore_conflicts=True)
            TaskkitTaskQueue.objects.bulk_create([
                self._db_task_to_queue(t) for t in objects
            ], ignore_conflicts=True)

    def retry_task(self, task: Task):
        with atomic():
            self._discard_tasks(task.id)
            db_task = self._task_to_db(task)
            db_task.save()
            self._db_task_to_queue(db_task).save()

    def get_queued_tasks(self, group: str, limit: int) -> list[Task]:
        return [
            self._db_to_task(t) for t in TaskkitTask.objects
            .filter(began__isnull=True, group=group)
            .order_by('due')[:limit]
        ]

    def lookup_tasks(self, *task_ids: str) -> dict[str, Optional[Task]]:
        tasks = {
            t.pk: self._db_to_task(t)
            for t in TaskkitTask.objects.filter(pk__in=task_ids)
        }
        return {tid: tasks.get(tid) for tid in task_ids}

    def assign_task(self, group: str, worker_id: str) -> Optional[Task]:
        if not self.use_queue_table:
            return self._assign_task(group, worker_id)

        n = 8
        while True:
            pks = list(
                TaskkitTaskQueue.objects
                .filter(group=group, due__lt=cur_ts())
                .values_list('pk', flat=True)
                .order_by('due')[:n])
            for pk in pks:
                if (task := self._assign(pk, worker_id)):
                    return task
            if len(pks) < n:
                return None
            n *= 2

    def _assign_task(self, group: str, worker_id: str) -> Optional[Task]:
        n = 8
        while True:
            pks = list(
                TaskkitTask.objects
                .filter(began__isnull=True, group=group, due__lt=cur_ts())
                .values_list('pk', flat=True)
                .order_by('due')[:n])
            for pk in pks:
                if (task := self._assign(pk, worker_id)):
                    return task
            if len(pks) < n:
                return None
            n *= 2

    def _assign(self, task_id: str, worker_id: str) -> Optional[Task]:
        with atomic():
            try:
                db_task = TaskkitTask.objects\
                    .select_for_update(skip_locked=True)\
                    .get(pk=task_id)
                TaskkitTaskQueue.objects.filter(id=task_id).delete()
                if db_task.began is None:
                    db_task.assignee_worker_id = worker_id
                    db_task.began = cur_ts()
                    db_task.save()
                    return self._db_to_task(db_task)
            except (OperationalError, TaskkitTask.DoesNotExist):
                pass
        return None

    def discard_tasks(self, *task_ids: str):
        with atomic():
            self._discard_tasks(*task_ids)

    def _discard_tasks(self, *task_ids: str):
        TaskkitTask.objects.filter(pk__in=task_ids).delete()
        TaskkitTaskQueue.objects.filter(pk__in=task_ids).delete()

    def succeed(self, task: Task, result: bytes):
        with atomic():
            try:
                db_task = TaskkitTask.objects\
                    .select_for_update()\
                    .get(pk=task.id)
            except TaskkitTask.DoesNotExist:
                db_task = self._task_to_db(task)
            db_task.done = cur_ts()
            db_task.began = db_task.began or db_task.done
            db_task.result = result
            db_task.disposable = db_task.done + task.ttl
            db_task.save()

    def fail(self, task: Task, error_message: str):
        with atomic():
            try:
                db_task = TaskkitTask.objects\
                    .select_for_update()\
                    .get(pk=task.id)
            except TaskkitTask.DoesNotExist:
                db_task = self._task_to_db(task)

            db_task.done = cur_ts()
            db_task.began = db_task.began or db_task.done
            db_task.error_message = error_message
            db_task.disposable = db_task.done + task.ttl
            db_task.save()

    def get_result(self, task_id: str) -> tuple[Task, bytes]:
        try:
            db_task = TaskkitTask.objects.get(pk=task_id)
            task = self._db_to_task(db_task)
            if db_task.done is None:
                raise NoResult(task)
            if db_task.result is None:
                raise Failed(task, db_task.error_message)
            return (task, db_task.result)
        except TaskkitTask.DoesNotExist:
            raise NotFound

    def get_done_task_ids(self,
                          since: Optional[float],
                          until: Optional[float],
                          limit: int) -> list[str]:
        return list(
            TaskkitTask.objects
            .filter(done__isnull=False,
                    done__gte=since or 0,
                    done__lte=until or cur_ts())
            .values_list('pk', flat=True)
            .order_by('done')[:limit]
        )

    def get_disposable_task_ids(self, limit: int) -> list[str]:
        return list(
            TaskkitTask.objects
            .filter(disposable__isnull=False,
                    disposable__lt=cur_ts())
            .values_list('pk', flat=True)
            .order_by('disposable')[:limit]
        )

    def get_stage_info(self, limit: int) -> list[StageInfo]:
        return [
            StageInfo(t.assignee_worker_id, t.pk, t.began)
            for t in TaskkitTask.objects
            .filter(done__isnull=True, began__isnull=False)
            .only('pk', 'assignee_worker_id', 'began')
            .order_by('began')[:limit]
        ]

    def restore(self, info: StageInfo):
        try:
            task = TaskkitTask.objects\
                .filter(began__isnull=False)\
                .get(pk=info.task_id)
            task.began = None
            task.save()
            self._db_task_to_queue(task).save()
        except TaskkitTask.DoesNotExist:
            pass

    def get_lock(self, target: str) -> Lock:
        return DjangoLock(target)

    def persist_scheduler_state_and_put_tasks(self,
                                              name: str,
                                              data: bytes,
                                              *tasks: Task):
        with atomic():
            try:
                state = TaskkitSchedulerState.objects.get(id=name)
                state.data = data
                state.save()
            except TaskkitSchedulerState.DoesNotExist:
                TaskkitSchedulerState.objects.create(id=name, data=data)
            self.put_tasks(*tasks)

    def get_scheduler_state(self, name: str) -> Optional[bytes]:
        return TaskkitSchedulerState.objects\
            .filter(pk=name)\
            .values_list('data', flat=True)\
            .first()

    def destroy_all(self):
        with atomic():
            TaskkitLock.objects.all().delete()
            TaskkitWorker.objects.all().delete()
            TaskkitTask.objects.all().delete()
            TaskkitSchedulerState.objects.all().delete()

    def _db_to_task(self, t: TaskkitTask) -> Task:
        return Task(
            id=t.pk,
            name=t.name,
            data=t.data,
            due=t.due,
            created=t.created,
            scheduled=t.scheduled,
            retry_count=t.retry_count,
            ttl=t.ttl,
        )

    def _task_to_db(self, t: Task) -> TaskkitTask:
        return TaskkitTask(
            id=t.id,
            group=t.group,
            name=t.name,
            data=t.data,
            due=t.due,
            created=t.created,
            scheduled=t.scheduled,
            retry_count=t.retry_count,
            ttl=t.ttl,
            assignee_worker_id=None,
            began=None,
            result=None,
            done=None,
            disposable=None,
        )

    def _db_task_to_queue(self, t: TaskkitTask) -> TaskkitTaskQueue:
        return TaskkitTaskQueue(id=t.id, group=t.group, due=t.due)


def make_kit(handler: TaskHandler, use_queue_table: bool = True) -> Kit:
    return Kit(DjangoBackend(use_queue_table), DjangoEventBridge(), handler)
