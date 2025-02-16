# Taskkit

[![PyPI](https://img.shields.io/pypi/v/taskkit)](https://pypi.org/project/taskkit/)  
[![GitHub](https://img.shields.io/github/stars/saryou/taskkit?style=social)](https://github.com/saryou/taskkit)

**Taskkit** is an experimental, lightweight distributed task runner designed as an alternative to [Celery](https://github.com/celery/celery), with improved resource efficiency for handling asynchronous tasks.

## Motivation

At [Nailbook](https://nailbook.jp/), we initially used [Celery](https://github.com/celery/celery) for asynchronous task processing. However, Celery assigns one process per worker, which resulted in inefficient resource utilization—especially since most tasks in Nailbook are I/O-bound.

To solve this issue, we developed **Taskkit**, a task runner that enables worker execution on a per-thread basis. This approach optimizes resource usage, making it more efficient for I/O-heavy workloads.

## Limitations

Taskkit has been running in production at Nailbook for over two years. However, it has only been extensively tested in a Django-based backend using MySQL. **Other backends, such as Redis, may not work as expected.**  

As a result, **Taskkit remains highly experimental**, and its functionality outside of this specific environment has not been thoroughly verified. **Use at your own discretion.**

## Installation

You can install Taskkit via pip:

```sh
pip install taskkit
```

## How to use

### 1. Implement TaskHandler

This is the core part.

```python
import json
from typing import Any
from taskkit import TaskHandler, Task, DiscardTask


class Handler(TaskHandler):
    def handle(self, task: Task):
        # Use `tagk.group` and `task.name` to determine how to handle the task
        if task.group == '...':
            if task.name == 'foo':
                # decode the data which encoded by `self.encode_data` if needed
                data = json.loads(task.data)
                # do something with the `data`
                ...
                # return result for the task
                return ...

            elif task.name == 'bar':
                # do something
                return ...

        # you should raise DiscardTask if you want to discard the task
        raise DiscardTask

    def get_retry_interval(self,
                           task: Task,
                           exception: Exception) -> float | None:
        # This method will be called if the handle method raises exceptions. You
        # should return how long time should be wait to retry the task in seconds
        # as float. If you don't want to retry the task, you can return None to
        # make the task fail or raise DiscardTask to discard the task.
        return task.retry_count if task.retry_count < 10 else None

    def encode_data(self, group: str, task_name: str, data: Any) -> bytes:
        # encode data of tasks for serializing it
        return json.dumps(data).encode()

    def encode_result(self, task: Task, result: Any) -> bytes:
        # encode the result of the task
        return json.dumps(result).encode()

    def decode_result(self, task: Task, encoded: bytes) -> Any:
        # decode the result of the task
        return json.loads(encoded)
```

### 2. Make Kit

#### Use redis impl

You can use redis backend like this:

```python
from redis.client import Redis
from taskkit.impl.redis import make_kit

REDIS_HOST = '...'
REDIS_PORT = '...'

redis = Redis(host=REDIS_HOST, port=REDIS_PORT)
kit = make_kit(redis, Handler())
```

#### Use django impl

1. Add `'taskkit.contrib.django'` to `INSTALLED_APPS` in the settings
2. Run `python manage.py migrate`
3. Make a `kit` instance like below:


```python
from taskkit.impl.django import make_kit

kit = make_kit(Handler())
```


### 3. Run workers

```python
GROUP_NAME = 'Any task group name'

# it starts busy loop
kit.start(
    # number of processes
    num_processes=3,
    # number of worker threads per process
    num_worker_threads_per_group={GROUP_NAME: 3})

# you can use `start_processes` to avoid busy loop
kit.start_processes(
    num_processes=3,
    num_worker_threads_per_group={GROUP_NAME: 3},
    daemon=True)
```


### 4. Initiate task

```python
from datetime import timedelta
from taskkit import ResultGetTimedOut


result = kit.initiate_task(
    GROUP_NAME,
    # task name
    'your task name',
    # task data which can be encoded by `Handler.encode_data`
    dict(some_data=1),
    # run the task after 10 or more seconds.
    due=datetime.now() + timedelta(seconds=10))

try:
    value = result.get(timeout=10)
except ResultGetTimedOut:
    ...
```

### Scheduled Tasks

```python
from datetime import timezone, timedelta
from taskkit import ScheduleEntry, ScheduleEntryDict, RegularSchedule, ScheduleEntriesCompatMapping

# define entries
# key is a name for scheduler
# value is a list of instances of ScheduleEntry
#       or a list of dicts conforming to ScheduleEntryDict
# 
# ScheduleEntryCompat: TypeAlias = ScheduleEntry | ScheduleEntryDict
# ScheduleEntriesCompat: TypeAlias = Sequence[ScheduleEntryCompat]
# ScheduleEntriesCompatMapping: TypeAlias = Mapping[str, ScheduleEntriesCompat]
#
schedule_entries: ScheduleEntriesCompatMapping = {
    'scheduler_name': [
        # You can use ScheduleEntry instance as follows. Note that the data
        # MUST be encoded by the same algorithm as `Handler.encode_data`.
        ScheduleEntry(
            # A key which can identify the schedule in the list
            key='...',
            # group name
            group=GROUP_NAME,
            # task name
            name='test2',

            # The data MUST BE encoded by the same algorithm as
            # `Handler.encode_data` so it would looks like:
            data=Handler.encode_data(GROUP_NAME, 'test2', 'SOME DATA'),

            # It means that the scheduler will initiate the task twice
            # an hour at **:00:00 and **:30:00.
            schedule=RegularSchedule(
                seconds={0},
                minutes={0, 30},
            ),
        ),

        # You can use dict form of schedule entry (recommended).
        # Note that in dict form, the data MUST NOT be encoded because `Kit`
        # takes care of encoding for convenience. Other properties are same
        # as ScheduleEntry. Also you can use ScheduleEntryDict for annotation.
        {
            'key': '...',
            'group': GROUP_NAME,
            'name': 'test3',

            # IT MUST NOT BE ENCODED
            'data': 2,

            'schedule': RegularSchedule(seconds={0}, minutes={30}),
        }
    ],

    # You can have multiple schedulers
    'another_scheduler': [
        # other entries ...
    ],
}

# pass the entries with kit.start
kit.start(
    num_processes=3,
    num_worker_threads_per_group={GROUP_NAME: 3},

    schedule_entries=schedule_entries,
    tzinfo=timezone(timedelta(hours=9), 'JST'))
```
