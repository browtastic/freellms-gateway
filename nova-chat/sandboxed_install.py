import json
import shutil
import subprocess
import time
SANDBOX_DIR = '/srv/skills-sandbox'
INSTALL_LOG = '/var/log/cloudaiwallet/install-requests.jsonl'
NOBODY_UID = '65534'
NOBODY_GID = '65534'
_SAFE_ENV = {'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': SANDBOX_DIR, 'LANG': 'C.UTF-8', 'TMPDIR': SANDBOX_DIR + '/tmp'}

def _have(cmd):
    return shutil.which(cmd) is not None

def _wrap(inner_sh):
    setpriv = ['setpriv', '--reuid', NOBODY_UID, '--regid', NOBODY_GID, '--clear-groups', '--no-new-privs']
    if _have('unshare') and _have('setpriv'):
        return ['unshare', '--mount', '--uts', '--fork'] + setpriv + ['/bin/sh', '-c', inner_sh]
    if _have('setpriv'):
        return setpriv + ['/bin/sh', '-c', inner_sh]
    return ['su', '-s', '/bin/sh', 'nobody', '-c', inner_sh]

def _build_cmd(slug):
    safe_slug = "'" + str(slug).replace("'", "'\\''") + "'"
    inner = f'ulimit -t 60 -f 65536 -u 64 -c 0 2>/dev/null; mkdir -p "$TMPDIR" 2>/dev/null; exec clawhub install {safe_slug} --force'
    return _wrap(inner)

def run_install_sandboxed(slug, prov):
    rec = {'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'slug': slug, 'category': 'EV-103', 'sandboxed': True}
    rec.update(prov or {})
    cmd = _build_cmd(slug)
    rec['sandbox_argv0'] = cmd[0]
    try:
        out = subprocess.run(cmd, cwd=SANDBOX_DIR, env=_SAFE_ENV, capture_output=True, text=True, timeout=120)
        rec['stdout'] = (out.stdout or '')[:20000]
        rec['stderr'] = (out.stderr or '')[:20000]
        rec['returncode'] = out.returncode
        ok = out.returncode == 0
        msg = (out.stdout or out.stderr or '').strip()[:4000] or '(no output)'
    except subprocess.TimeoutExpired:
        rec['error'] = 'timeout'
        ok = False
        msg = 'Install timed out.'
    except Exception as e:
        rec['error'] = str(e)
        ok = False
        msg = 'Install error.'
    try:
        with open(INSTALL_LOG, 'a') as lf:
            lf.write(json.dumps(rec, default=str) + '\n')
    except Exception:
        pass
    return (ok, msg)
if __name__ == '__main__':
    probe = _wrap('echo "uid=$(id -u) user=$(whoami)"; echo "AWS_KEY=[${AWS_ACCESS_KEY_ID:-empty}]"; echo "meta=$(curl -s -m3 -o /dev/null -w %{http_code} http://169.254.169.254/latest/meta-data/ 2>/dev/null || echo blocked)"')
    r = subprocess.run(probe, cwd=SANDBOX_DIR, env=_SAFE_ENV, capture_output=True, text=True, timeout=30)
    print('primitive:', probe[0])
    print(r.stdout or '(no stdout)', end='')
    if r.stderr:
        print('STDERR:', r.stderr, end='')
    print('EXPECT: uid=65534 user=nobody | AWS_KEY=[empty] | meta=blocked (after iptables IMDS rule)')
