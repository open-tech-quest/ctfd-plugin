"""Check that credential generation cannot replace existing runtime secrets."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import urlsplit

SCRIPT = Path(__file__).with_name('create-deploy-env.py')

class DeploymentCredentials(unittest.TestCase):
    def generate(self, path):
        return subprocess.run([sys.executable, str(SCRIPT), '--output', str(path)],
                              capture_output=True, text=True)

    def test_private_complete_credentials_and_consistent_connection_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ctfd.env'
            result = self.generate(path)
            self.assertEqual(result.returncode, 0)
            values = dict(line.split('=', 1) for line in path.read_text().splitlines())
            self.assertEqual(set(values), {'SECRET_KEY', 'DATABASE_URL', 'REDIS_URL', 'MARIADB_PASSWORD',
                'MARIADB_ROOT_PASSWORD', 'REDIS_PASSWORD', 'CTFD_ADMIN_PASSWORD'})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            db = urlsplit(values['DATABASE_URL'])
            cache = urlsplit(values['REDIS_URL'])
            self.assertTrue(db.password == values['MARIADB_PASSWORD'], 'Database credentials disagree')
            self.assertTrue(cache.password == values['REDIS_PASSWORD'], 'Cache credentials disagree')
            self.assertEqual((db.hostname, db.port, db.path), ('database', 3306, '/ctfd'))
            self.assertEqual((cache.hostname, cache.port, cache.path), ('cache', 6379, '/0'))
            for key in ['SECRET_KEY','MARIADB_PASSWORD','MARIADB_ROOT_PASSWORD','REDIS_PASSWORD','CTFD_ADMIN_PASSWORD']:
                self.assertTrue(len(values[key]) >= 48 and all(c in '0123456789abcdef' for c in values[key]))
                self.assertTrue(values[key] not in result.stdout + result.stderr, 'Credential value was printed')

    def test_second_run_does_not_rotate_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ctfd.env'
            self.assertEqual(self.generate(path).returncode, 0)
            original = path.read_bytes()
            self.assertNotEqual(self.generate(path).returncode, 0)
            self.assertTrue(path.read_bytes() == original, 'Existing credentials were replaced')

    def test_existing_symlink_does_not_overwrite_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'retained.env'
            target.write_text('retained')
            link = Path(directory) / 'ctfd.env'
            os.symlink(target, link)
            self.assertNotEqual(self.generate(link).returncode, 0)
            self.assertEqual(target.read_text(), 'retained')

if __name__ == '__main__':
    unittest.main()
