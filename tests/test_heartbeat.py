import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.services.heartbeat import HeartbeatLoop, send_heartbeat


class HeartbeatTests(unittest.TestCase):
    def test_disabled_without_server_or_machine(self):
        self.assertFalse(send_heartbeat("", "M", {"platforms": {}}))
        self.assertFalse(send_heartbeat("https://x", "", {"platforms": {}}))

    def test_loop_builds_payload_and_stops(self):
        calls = []
        def build():
            calls.append(1)
            return "https://127.0.0.1:9", "M", {"platforms": {"alibaba": {"status": "空闲"}}}
        loop = HeartbeatLoop(build, interval=30)
        loop.start()
        import time
        time.sleep(0.3)
        loop.stop()
        self.assertTrue(calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
