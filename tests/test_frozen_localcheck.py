from pathlib import Path
import sys
import socket
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from aeclone.localcheck import run

class LocalCheckTests(unittest.TestCase):
    def test_packaging_smoke_checks_need_no_network_or_shop(self):
        with patch.object(socket.socket,"connect",side_effect=AssertionError("network forbidden")):
            self.assertEqual(run(),0)

if __name__=='__main__':unittest.main(verbosity=2)
