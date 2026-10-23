import asyncio
import os
import subprocess
import time
JAILRUN = '/usr/local/bin/jailrun.sh'
JAIL_HOME = '/var/lib/jailhome'
MAX_OUT = 65536
_CONCURRENCY = 3
RATE_PER_MIN = 20
_sem = asyncio.Semaphore(_CONCURRENCY)
_RATE: dict[str, list[float]] = {}

def _rate_ok(key: str) -> bool:
    now = time.time()
    wins = [t for t in _RATE.get(key, []) if now - t < 60]
    if len(wins) >= RATE_PER_MIN:
        _RATE[key] = wins
        return False
    wins.append(now)
    _RATE[key] = wins
    return True

def jail_available() -> bool:
    return os.path.exists(JAILRUN)

async def _run_async(argv: list[str], timeout: int) -> dict:
    timeout = max(1, min(int(timeout or 20), 30))
    async with _sem:
        t0 = time.time()
        proc = await asyncio.create_subprocess_exec('sudo', '-n', '-u', 'nobody', JAILRUN, str(timeout), JAIL_HOME, '--', *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        timed_out = False
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout + 5)
        except asyncio.TimeoutError:
            proc.kill()
            out, err = await proc.communicate()
            timed_out = True
        return {'stdout': (out or b'').decode(errors='replace')[:MAX_OUT], 'stderr': (err or b'').decode(errors='replace')[:MAX_OUT], 'exit_code': proc.returncode if proc.returncode is not None else -1, 'timed_out': timed_out, 'duration_ms': (time.time() - t0) * 1000}

def _run_sync(argv: list[str], timeout: int) -> dict:
    timeout = max(1, min(int(timeout or 20), 30))
    t0 = time.time()
    try:
        p = subprocess.run(['sudo', '-n', '-u', 'nobody', JAILRUN, str(timeout), JAIL_HOME, '--', *argv], capture_output=True, timeout=timeout + 5)
        return {'stdout': p.stdout.decode(errors='replace')[:MAX_OUT], 'stderr': p.stderr.decode(errors='replace')[:MAX_OUT], 'exit_code': p.returncode, 'timed_out': False, 'duration_ms': (time.time() - t0) * 1000}
    except subprocess.TimeoutExpired as e:
        return {'stdout': (e.stdout or b'').decode(errors='replace')[:MAX_OUT], 'stderr': (e.stderr or b'').decode(errors='replace')[:MAX_OUT], 'exit_code': -1, 'timed_out': True, 'duration_ms': (time.time() - t0) * 1000}

def _limited() -> dict:
    return {'stdout': '', 'stderr': 'too many requests — slow down\n', 'exit_code': 125, 'timed_out': False, 'duration_ms': 0.0, 'rate_limited': True}

async def jail_shell(command: str, timeout: int=20, rate_key: str='global') -> dict:
    if not _rate_ok(rate_key):
        return _limited()
    return await _run_async(['/bin/bash', '-c', command], timeout)

async def jail_argv(argv: list, timeout: int=20, rate_key: str='global') -> dict:
    if not _rate_ok(rate_key):
        return _limited()
    return await _run_async([str(a) for a in argv], timeout)

def jail_shell_sync(command: str, timeout: int=20, rate_key: str='global') -> dict:
    if not _rate_ok(rate_key):
        return _limited()
    return _run_sync(['/bin/bash', '-c', command], timeout)

def format_result(command_display: str, res: dict) -> str:
    parts = []
    if command_display:
        parts.append(f'$ {command_display}')
    if res.get('stdout'):
        parts.append(res['stdout'].rstrip('\n'))
    if res.get('stderr'):
        parts.append(res['stderr'].rstrip('\n'))
    if res.get('timed_out'):
        parts.append('(killed: timeout)')
    parts.append(f"(exit {res.get('exit_code')})")
    return '\n'.join(parts)
