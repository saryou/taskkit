import os

from django.test import TestCase
from redis import Redis

from taskkit.impl import redis, django
from taskkit.kit import Kit
from taskkit.task import TaskHandler

from .test_backend import BackendTests
from .test_kit import KitTests


_redis = Redis(host=os.environ.get('REDIS_HOST', '127.0.0.1'),
               port=int(os.environ.get('REDIS_PORT', '6379')))


class RedisBackendTests(BackendTests):
    backend = redis.RedisBackend(_redis)


class DjangoBackendTests(TestCase, BackendTests):
    backend = django.DjangoBackend(use_old_queue=False)


class RedisKitTests(KitTests):
    def make_kit(self, handler: TaskHandler) -> Kit:
        return redis.make_kit(_redis, handler)


class DjangoKitTests(TestCase, KitTests):
    def make_kit(self, handler: TaskHandler) -> Kit:
        return django.make_kit(handler)
