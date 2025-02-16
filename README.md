# Taskkit

**Taskkit** is an experimental, lightweight distributed task runner designed as an alternative to [Celery](https://github.com/celery/celery), with improved resource efficiency for handling asynchronous tasks.

- **GitHub**: [https://github.com/saryou/taskkit](https://github.com/saryou/taskkit)  
- **PyPI**: [https://pypi.org/project/taskkit/](https://pypi.org/project/taskkit/)


## Motivation

At [Nailbook](https://nailbook.jp/), we initially used [Celery](https://github.com/celery/celery) for asynchronous task processing. However, Celery assigns one process per worker, which resulted in inefficient resource utilization—especially since most tasks in Nailbook are I/O-bound.

To solve this issue, we developed **Taskkit**, a task runner that enables worker execution on a per-thread basis. This approach optimizes resource usage, making it more efficient for I/O-heavy workloads.

## Design Philosophy

Taskkit is designed with an extremely **simple API** in mind.  
All major APIs **(except for encoding/decoding of task data)** are fully type-annotated using Python’s `typing` module.  

This ensures:
- **Low implementation cost** – The API remains lightweight and easy to integrate.
- **Readability & predictability** – Developers can quickly understand the expected behavior of tasks.

By prioritizing type safety and simplicity, Taskkit provides a clear and minimalistic approach to distributed task execution.

## Implementation Details

Taskkit uses a **backend-based queue** to manage tasks. Each worker:
1. Fetches the **oldest due task** from the queue.
2. Assigns the task to itself for execution.
3. Processes the task asynchronously.

### Scalability Considerations

- This approach allows **scaling by adding more workers**, which increases processing capacity.
- However, **the backend queue and the exclusivity of task assignment can become bottlenecks** under high loads.

### Proven Performance

One production deployment of Taskkit operates on a shared Amazon Aurora database cluster.
This configuration typically incurs a total cost of around $2,000 per month, depending on scale and usage.
In this environment, Taskkit has been observed to reliably process up to 100 tasks per second alongside other database operations.

## Limitations

Taskkit has been running in production at Nailbook for over two years. However, it has been extensively tested **only** in a Django-based backend using **Aurora MySQL**.  

While an experimental Redis implementation is available, **it has never been used in production, and its stability is not guaranteed**. Other backends may also not work as expected.

As a result, **Taskkit remains highly experimental**, and its functionality outside of this specific environment has not been thoroughly verified. **Use at your own discretion.**

## How to use

### Installation

You can install Taskkit via pip:

```sh
pip install taskkit
```

### 1. Implement TaskHandler

Each task must be handled by a `TaskHandler` implementation.  
This class defines how Taskkit should process tasks, encode/decode data, and handle retries.

```python
import json
from typing import Any
from taskkit import TaskHandler, Task, DiscardTask


class Handler(TaskHandler):
    def handle(self, task: Task) -> Any:
        # Use `tagk.group` and `task.name` to determine how to handle the task
        # If it returns any data, it must be encodable by `self.encode_result`.
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

#### Using Redis Backend (Experimental)

An experimental Redis backend is available, but **it has never been used in production, and its stability is not guaranteed**.

If you would like to try it, you can use the following setup:

```python
from redis.client import Redis
from taskkit.impl.redis import make_kit

REDIS_HOST = '...'
REDIS_PORT = '...'

redis = Redis(host=REDIS_HOST, port=REDIS_PORT)
kit = make_kit(redis, Handler())
```

#### Using Django Backend

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
