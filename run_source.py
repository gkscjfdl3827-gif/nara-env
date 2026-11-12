"""Independent source entry point. Dry runs use an isolated copy and never POST alerts."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch


def run(source, dry_run=False):
    if dry_run:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(__file__).resolve().parent
            shutil.copytree(root / 'core', Path(tmp) / 'core')
            for name in ['cloud_run.py', 'run_source.py', 'config.json',
                         'nara_notices.db', 'alio_notices.db', 'eiaa_notices.db']:
                if (root / name).exists():
                    shutil.copy2(root / name, Path(tmp) / name)
            env = dict(os.environ, MONITOR_NO_SEND='1', PYTHONUNBUFFERED='1')
            subprocess.run([sys.executable, str(Path(tmp) / 'run_source.py'),
                            '--source', source], cwd=tmp, env=env, check=True)
        return
    if source == 'nara':
        from cloud_run import run_cloud_check
        from core.notifier import MultiChannelNotifier
        if os.environ.get('MONITOR_NO_SEND') == '1':
            with patch.object(MultiChannelNotifier, 'notify_all', return_value={'ntfy': True, 'dry_run': True}):
                run_cloud_check(include_auxiliary=False)
        else:
            run_cloud_check(include_auxiliary=False)
    else:
        if source == 'alio':
            from core.alio_collector import AlioCollector as Collector, run_alio_cloud_check as check
        else:
            from core.eiaa_collector import EiaaCollector as Collector, run_eiaa_cloud_check as check
        if os.environ.get('MONITOR_NO_SEND') == '1':
            with patch.object(Collector, 'send_ntfy_alert', return_value=True):
                check()
        else:
            check()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True, choices=['nara', 'alio', 'eiaa'])
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    run(args.source, args.dry_run)
