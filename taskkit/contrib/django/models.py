from django.db import models


class TaskkitControlEvent(models.Model):
    class Meta:
        db_table = 'taskkit_control_event'
        app_label = 'taskkit'

    sent: models.FloatField = models.FloatField(db_index=True)
    data: models.BinaryField = models.BinaryField()


class TaskkitLock(models.Model):
    class Meta:
        db_table = 'taskkit_lock'
        app_label = 'taskkit'

    id: models.CharField = models.CharField(primary_key=True, max_length=255)


class TaskkitWorker(models.Model):
    class Meta:
        db_table = 'taskkit_worker'
        app_label = 'taskkit'

    id: models.CharField = models.CharField(primary_key=True, max_length=255)
    expires: models.FloatField = models.FloatField(db_index=True)


class TaskkitTask(models.Model):
    class Meta:
        db_table = 'taskkit_task'
        app_label = 'taskkit'
        indexes = [
            models.Index(fields=['began', 'group', 'due']),
            models.Index(fields=['done', 'began']),
            models.Index(fields=['name', 'created']),
        ]

    id: models.CharField = models.CharField(primary_key=True, max_length=255)
    group: models.CharField = models.CharField(max_length=40)
    name: models.CharField = models.CharField(max_length=255)
    data: models.BinaryField = models.BinaryField()
    due: models.FloatField = models.FloatField()
    created: models.FloatField = models.FloatField()
    scheduled: models.FloatField = models.FloatField(null=True)
    retry_count: models.PositiveIntegerField = models.PositiveIntegerField()
    ttl: models.FloatField = models.FloatField()

    assignee_worker_id: models.CharField = models.CharField(null=True, max_length=255)
    began: models.FloatField = models.FloatField(null=True)

    result: models.BinaryField = models.BinaryField(null=True)
    error_message: models.TextField = models.TextField(null=True)
    done: models.FloatField = models.FloatField(null=True)
    disposable: models.FloatField = models.FloatField(null=True, db_index=True)


class TaskkitTaskQueue(models.Model):
    class Meta:
        db_table = 'taskkit_task_queue'
        app_label = 'taskkit'
        indexes = [
            models.Index(fields=['group', 'due']),
        ]

    id: models.CharField = models.CharField(primary_key=True, max_length=255)
    group: models.CharField = models.CharField(max_length=40)
    due: models.FloatField = models.FloatField()


class TaskkitSchedulerState(models.Model):
    class Meta:
        db_table = 'taskkit_scheduler_state'
        app_label = 'taskkit'

    id: models.CharField = models.CharField(primary_key=True, max_length=255)
    data: models.BinaryField = models.BinaryField()
