import unittest
from unittest.mock import patch

from services.approval_lock import approval_lock


class ApprovalLockTests(unittest.TestCase):
    def test_same_batch_cannot_be_locked_twice(self):
        with approval_lock("batch-1"):
            with self.assertRaises(RuntimeError):
                with approval_lock("batch-1"):
                    self.fail("Second approval lock should not be acquired.")

    def test_different_batches_can_be_locked_independently(self):
        with approval_lock("batch-1"):
            with approval_lock("batch-2"):
                self.assertTrue(True)

    def test_redis_configuration_does_not_silently_fall_back_to_local_lock(self):
        with patch.dict("os.environ", {"REDIS_URL": "redis://unavailable.invalid:6379/0"}):
            with patch("redis.Redis.from_url", side_effect=ConnectionError("offline")):
                with self.assertRaises(ConnectionError):
                    with approval_lock("batch-3"):
                        self.fail("Should fail closed when distributed lock is unavailable.")


if __name__ == "__main__":
    unittest.main()
