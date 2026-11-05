"""Sanitized portfolio adaptation. Dry run by default; no work at import time."""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import time

LOG = logging.getLogger('ips-update')
COMMANDS = {'login', 'run-ips-update', 'show-task', 'publish', 'logout'}


def load_config(path):
    config = json.loads(Path(path).read_text(encoding='utf-8'))
    required = {'package_path', 'known_hosts', 'ssh_username', 'ssh_key_path', 'servers'}
    if not isinstance(config, dict) or not required <= config.keys():
        raise ValueError('Missing configuration fields')
    for field in required - {'servers'}:
        if not isinstance(config[field], str) or not config[field].strip():
            raise ValueError('Invalid configuration field')
    if not isinstance(config['servers'], list) or not config['servers']:
        raise ValueError('At least one server is required')
    if config.get('ca_bundle') is not None and not isinstance(config['ca_bundle'], str):
        raise ValueError('Invalid CA bundle')
    names, hosts = set(), set()
    for server in config['servers']:
        if not isinstance(server, dict):
            raise ValueError('Invalid server entry')
        for field in ('name', 'host', 'api_key_env', 'remote_path'):
            if not isinstance(server.get(field), str) or not server[field].strip():
                raise ValueError('Invalid server field')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', server['host']):
            raise ValueError('Use a hostname or IPv4 address without URL components')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', server['name']):
            raise ValueError('Use a simple non-sensitive server alias')
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', server['api_key_env']):
            raise ValueError('Invalid environment variable name')
        remote = PurePosixPath(server['remote_path'])
        if remote.parent != PurePosixPath('/var/log') or remote.suffix.lower() != '.upf':
            raise ValueError('Remote package must be an explicit /var/log/*.upf path')
        if server['name'] in names or server['host'] in hosts:
            raise ValueError('Duplicate server alias or host')
        names.add(server['name']); hosts.add(server['host'])
    return config


def api_call(session, host, command, payload, sid=None, verify=True):
    if command not in COMMANDS:
        raise ValueError('Unsupported command')
    response = session.post(f'https://{host}/web_api/{command}', json=payload,
                            headers={'X-chkp-sid': sid} if sid else {},
                            verify=verify, timeout=(10, 60), allow_redirects=False)
    if not 200 <= response.status_code < 300:
        raise RuntimeError('Management API rejected request')
    result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError('Unexpected API response')
    return result


def wait_for_task(call, task_id, timeout=900, interval=5, clock=time.monotonic, sleep=time.sleep):
    if not isinstance(task_id, str) or not task_id.strip():
        raise RuntimeError('Missing task identifier')
    deadline = clock() + timeout
    while clock() < deadline:
        tasks = call('show-task', {'task-id': task_id}).get('tasks')
        if not isinstance(tasks, list) or not tasks or not all(isinstance(t, dict) for t in tasks):
            raise RuntimeError('Missing task information')
        statuses = [task.get('status') for task in tasks]
        if any(status not in ('succeeded', 'failed', 'in progress') for status in statuses):
            raise RuntimeError('Unrecognized task status')
        if 'failed' in statuses:
            raise RuntimeError('Remote task failed')
        if all(status == 'succeeded' for status in statuses):
            return
        sleep(min(interval, max(0, deadline - clock())))
    raise TimeoutError('Task polling exceeded deadline; verify remote state before retry')


def process_server(config, server):
    import paramiko
    import requests
    key = os.environ.get(server['api_key_env'])
    if not key:
        raise ValueError('Required API key environment variable is missing')
    verify = config.get('ca_bundle') or True
    ssh = paramiko.SSHClient()
    ssh.load_host_keys(config['known_hosts'])
    ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
    sid = None
    with requests.Session() as session:
        session.trust_env = False
        try:
            ssh.connect(server['host'], username=config['ssh_username'],
                        key_filename=config['ssh_key_path'], look_for_keys=False,
                        allow_agent=False, timeout=15, auth_timeout=15, banner_timeout=15)
            with ssh.open_sftp() as sftp:
                sftp.get_channel().settimeout(60)
                # Refuse overwriting an existing package at the chosen path.
                with sftp.open(server['remote_path'], 'wx') as target:
                    with open(config['package_path'], 'rb') as source:
                        while chunk := source.read(1024 * 1024):
                            target.write(chunk)
            sid = api_call(session, server['host'], 'login', {'api-key': key}, verify=verify).get('sid')
            if not isinstance(sid, str) or not sid:
                raise RuntimeError('Login response did not contain a session')
            def call(command, payload):
                return api_call(session, server['host'], command, payload, sid, verify)
            update = call('run-ips-update', {'package-path': server['remote_path']})
            wait_for_task(call, update.get('task-id'))
            publish = call('publish', {})
            wait_for_task(call, publish.get('task-id'))
            with ssh.open_sftp() as sftp:
                sftp.get_channel().settimeout(60)
                sftp.remove(server['remote_path'])
            LOG.info('%s: update and publish completed; staged package removed', server['name'])
        finally:
            if sid:
                try:
                    api_call(session, server['host'], 'logout', {}, sid, verify)
                except Exception:
                    LOG.warning('%s: logout failed; check session state', server['name'])
            ssh.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config.example.json')
    parser.add_argument('--execute', action='store_true', help='Explicitly permit remote updates')
    parser.add_argument('--expected-sha256', help='Trusted independently obtained package digest')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    try:
        config = load_config(args.config)
        if not args.execute:
            for server in config['servers']:
                LOG.info('DRY RUN %s: upload -> update -> poll -> publish -> poll -> cleanup -> logout', server['name'])
            LOG.info('No network calls or package reads performed')
            return 0
        if not args.expected_sha256 or not re.fullmatch(r'[a-fA-F0-9]{64}', args.expected_sha256):
            raise ValueError('Execution requires a trusted SHA-256 digest')
        package = Path(config['package_path'])
        if not package.is_file() or package.suffix.lower() != '.upf':
            raise ValueError('Select an explicit UPF package')
        with package.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != args.expected_sha256.lower():
            raise ValueError('Package digest mismatch')
        for field in ('known_hosts', 'ssh_key_path'):
            if not Path(config[field]).is_file():
                raise ValueError('SSH trust or key file unavailable')
        if config.get('ca_bundle') and not Path(config['ca_bundle']).is_file():
            raise ValueError('CA bundle unavailable')
        if any(not os.environ.get(s['api_key_env']) for s in config['servers']):
            raise ValueError('Required API key environment variable is missing')
        for server in config['servers']:
            process_server(config, server)
        return 0
    except Exception as exc:
        # Do not log exception text, response bodies, API keys, session IDs or hosts.
        LOG.error('Run stopped (%s). Check remote state before retrying.', type(exc).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
