"""Exercise failure boundaries without starting Docker or touching a configured DB."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MOCK = """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$CALLS"
case "$*" in
    *"config --environment") cat "$APP_ENV_FILE" ;;
    "image inspect "*) echo sha256:candidate ;;
    *"ps -q postgres") echo pg-live ;;
    *"ps -q web") echo web-live ;;
    "inspect --format {{.State.Health.Status}} "*) echo healthy ;;
    "inspect --format {{.Image}} pg-live") echo sha256:postgres ;;
    "inspect --format {{.Image}} web-live") echo sha256:candidate ;;
    *"makemigrations --check --dry-run") [[ $SCENARIO != drift ]] ;;
    *"pg_dump --format=custom"*) [[ $SCENARIO != backup ]] || exit 1; echo dummy-archive ;;
    "network create "*) echo temporary-network ;;
    "run -d "*) echo temporary-db ;;
    "exec -i "*) cat >/dev/null; [[ $SCENARIO != restore ]] ;;
    *"-T migrate python manage.py migrate --noinput") [[ $SCENARIO != migrate ]] ;;
    *"--force-recreate "*) [[ $SCENARIO != health ]] ;;
esac
"""


class ControlledReleaseTests(unittest.TestCase):
    def test_release_gates(self):
        for scenario in ('success', 'drift', 'backup', 'restore', 'migrate', 'health'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                mock = root / 'docker'
                mock.write_text(MOCK)
                mock.chmod(0o700)
                env_file = root / 'app.env'
                env_file.write_text(f'COMPOSE_PROJECT_NAME=release-test-{root.name.lower()}\n')
                calls = root / 'calls'
                env = dict(os.environ, APP_ENV_FILE=str(env_file), CALLS=str(calls),
                           SCENARIO=scenario, PATH=temp + os.pathsep + os.environ['PATH'])
                result = subprocess.run(
                    [ROOT / 'release.sh', 'deploy', 'example:candidate', temp, '--maintenance'],
                    capture_output=True, text=True, env=env,
                )
                self.assertEqual(result.returncode == 0, scenario == 'success', result.stderr)
                log = calls.read_text()
                migrate = '-T migrate python manage.py migrate --noinput'
                self.assertEqual(log.count(migrate), int(scenario in ('success', 'migrate', 'health')))
                if scenario == 'drift':
                    self.assertNotIn('stop web', log)
                if scenario == 'success':
                    self.assertLess(log.index('pg_restore'), log.index(migrate))
                    self.assertLess(log.index(migrate), log.index('--force-recreate'))
                    self.assertEqual(len(list(root.glob('*.dump.image-id'))), 1)
                elif scenario != 'drift':
                    self.assertGreaterEqual(log.count('stop web'), 2)


if __name__ == '__main__':
    unittest.main()
