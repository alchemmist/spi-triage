"""Queue telemetry must not contend for SQLite's one WAL writer."""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from core import Store


class QueueReadConcurrencyTests(unittest.TestCase):
    def test_health_reads_during_open_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'queue.sqlite')
            with store.db() as writer:
                writer.execute('INSERT INTO dispatch VALUES(1,?,?,?)', ('reserved', 42, 1))
                self.assertEqual(store.health()['counts'], {})
                self.assertEqual(store.dispatch_status()['attempts'], 0)
                with store.read_db() as reader, self.assertRaisesRegex(sqlite3.OperationalError, 'readonly'):
                    reader.execute('DELETE FROM dispatch')
            self.assertEqual(store.dispatch_status()['attempts'], 1)

    @unittest.skipUnless(os.environ.get('SPI_TEST_PG_SOCKET'), 'isolated PostgreSQL fixture required')
    def test_postgres_reader_does_not_wait_for_writer(self):
        from unittest.mock import patch
        import psycopg
        url = 'host=' + os.environ['SPI_TEST_PG_SOCKET'] + ' dbname=postgres'
        with patch.dict(os.environ, SPI_POSTGRES_URL=url):
            store = Store(':memory:')
            with store.db() as writer:
                writer.execute('INSERT INTO dispatch VALUES(1,?,?,?)', ('reserved', 42, 1))
                self.assertEqual(store.health()['counts'], {})
                self.assertEqual(store.dispatch_status()['attempts'], 0)
                with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction), store.read_db() as reader:
                    reader.execute('DELETE FROM dispatch')
            self.assertEqual(store.dispatch_status()['attempts'], 1)
