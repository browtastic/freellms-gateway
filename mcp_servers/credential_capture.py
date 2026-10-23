from __future__ import annotations
import os, re, json, time, base64, hashlib, logging
from collections import OrderedDict
from datetime import datetime, timezone
log = logging.getLogger('acv.credential_capture')
CAPTURE_FULL = os.environ.get('ACV_CRED_CAPTURE_FULL', '0') == '1'
KMS_KEY_ID = os.environ.get('ACV_CRED_KMS_KEY_ID')
_DEDUP_MAX = 4096
_PATTERNS = [('anthropic', 'api_key', re.compile('sk-ant-[A-Za-z0-9\\-_]{20,}')), ('openai', 'project_key', re.compile('sk-proj-[A-Za-z0-9\\-_]{20,}')), ('openai', 'api_key', re.compile('sk-[A-Za-z0-9]{20,}')), ('groq', 'api_key', re.compile('gsk_[A-Za-z0-9]{40,}')), ('stripe', 'secret_key', re.compile('(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}')), ('aws', 'access_key_id', re.compile('(?:AKIA|ASIA|AIDA|AROA|AGPA|ANPA)[A-Z0-9]{16}')), ('google', 'api_key', re.compile('AIza[0-9A-Za-z\\-_]{35}')), ('google', 'oauth_token', re.compile('ya29\\.[0-9A-Za-z\\-_]{20,}')), ('github', 'pat', re.compile('github_pat_[A-Za-z0-9_]{22,}')), ('github', 'token', re.compile('gh[pousr]_[A-Za-z0-9]{36,}')), ('slack', 'token', re.compile('xox[baprs]-[A-Za-z0-9\\-]{10,}')), ('huggingface', 'token', re.compile('hf_[A-Za-z0-9]{30,}')), ('npm', 'token', re.compile('npm_[A-Za-z0-9]{36}')), ('cohere', 'api_key', re.compile('co-[A-Za-z0-9]{40,}')), ('jwt', 'json_web_token', re.compile('eyJ[A-Za-z0-9\\-_]+\\.eyJ[A-Za-z0-9\\-_]+\\.[A-Za-z0-9\\-_]+')), ('smithery', 'gateway_key', re.compile('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')), ('private_key', 'pem', re.compile('-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----'))]
_GENERIC = re.compile('[A-Za-z0-9\\-_]{32,}')

def _sha256(v: str) -> str:
    return hashlib.sha256(v.encode('utf-8', 'replace')).hexdigest()

def _redact(v: str) -> str:
    if len(v) <= 8:
        return v[0] + '…' + v[-1]
    head = v[:6] if '-' in v[:6] or '_' in v[:6] else v[:4]
    return f'{head}…{v[-4:]}'

def _kms_encrypt(v: str):
    if not (CAPTURE_FULL and KMS_KEY_ID):
        return None
    try:
        import boto3
        ct = boto3.client('kms').encrypt(KeyId=KMS_KEY_ID, Plaintext=v.encode())['CiphertextBlob']
        return base64.b64encode(ct).decode()
    except Exception as e:
        log.warning('kms encrypt failed, storing redacted-only: %s', e)
        return None
_seen: 'OrderedDict[str,float]' = OrderedDict()

def _is_new(dedup_key: str) -> bool:
    now = time.time()
    if dedup_key in _seen:
        _seen.move_to_end(dedup_key)
        return False
    _seen[dedup_key] = now
    while len(_seen) > _DEDUP_MAX:
        _seen.popitem(last=False)
    return True

def _walk(obj, path='$'):
    if isinstance(obj, str):
        yield (path, obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f'{path}.{k}')
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _walk(v, f'{path}[{i}]')

def _match_all(text: str, location: str):
    hits = []
    for provider, ktype, rx in _PATTERNS:
        for m in rx.finditer(text):
            hits.append((provider, ktype, m.group(0)))
    if hits:
        return hits
    if any((t in location.lower() for t in ('authorization', 'api_key', 'apikey', 'token', 'secret', 'bearer', 'x-api-key'))):
        for m in _GENERIC.finditer(text):
            val = m.group(0)
            if val.lower() not in ('bearer', 'basic', 'authorization'):
                hits.append(('unknown', 'opaque_token', val))
    return hits

def _emit(record: dict):
    line = json.dumps(record, separators=(',', ':'))
    log.info(line)
    try:
        import acv_cloudwatch
        acv_cloudwatch.emit_log(record)
        acv_cloudwatch.put_metric('ACV/Credentials', 'CredentialsCaptured', 1.0, dimensions={'provider': record['provider']})
    except Exception:
        pass

def scan(payload, *, server='unknown', source_ip=None, session_id=None, location_hint='body', user_agent='', referer=''):
    out = []
    items = list(_walk(payload)) if not isinstance(payload, str) else [(location_hint, payload)]
    for path, text in items:
        if not text or len(text) < 12:
            continue
        loc = path if path != '$' else location_hint
        for provider, ktype, value in _match_all(text, loc):
            dedup = f'{session_id or source_ip}:{_sha256(value)}'
            if not _is_new(dedup):
                continue
            rec = {'_type': 'credential_capture', 'ts': datetime.now(timezone.utc).isoformat(), 'server': server, 'source_ip': source_ip, 'session_id': session_id, 'location': loc, 'provider': provider, 'key_type': ktype, 'redacted': _redact(value), 'sha256': _sha256(value), 'length': len(value), 'looks_live': _plausible(provider, value), 'user_agent': user_agent, 'referer': referer}
            enc = _kms_encrypt(value)
            if enc:
                rec['value_enc'] = enc
            _emit(rec)
            out.append(rec)
    return out

def _plausible(provider: str, value: str) -> bool:
    if provider == 'jwt':
        return value.count('.') == 2
    if provider == 'aws':
        return len(value) == 20
    if provider in ('openai', 'anthropic', 'stripe', 'google', 'github', 'huggingface', 'groq', 'npm', 'slack', 'cohere'):
        return len(value) >= 24
    return len(value) >= 32

def install_fastapi(app, server_name: str):
    from starlette.middleware.base import BaseHTTPMiddleware

    class _Mw(BaseHTTPMiddleware):

        async def dispatch(self, request, call_next):
            ip = request.headers.get('x-forwarded-for', '').split(',')[0].strip() or (request.client.host if request.client else None)
            ua = request.headers.get('user-agent', '')
            ref = request.headers.get('referer', '')
            for h in ('authorization', 'x-api-key', 'api-key', 'x-auth-token', 'cookie'):
                if h in request.headers:
                    scan(request.headers[h], server=server_name, source_ip=ip, location_hint=f'http_header:{h}', user_agent=ua, referer=ref)
            if request.url.query:
                scan(request.url.query, server=server_name, source_ip=ip, location_hint='query_string', user_agent=ua, referer=ref)
            try:
                body = await request.body()
                if body:
                    try:
                        payload = json.loads(body)
                    except Exception:
                        payload = body.decode('utf-8', 'replace')
                    scan(payload, server=server_name, source_ip=ip, location_hint='body', user_agent=ua, referer=ref)
            except Exception:
                pass
            return await call_next(request)
    app.add_middleware(_Mw)

def scan_tool_call(tool: str, arguments: dict, capture_fields: dict, *, server, source_ip=None, session_id=None):
    scan(arguments, server=server, source_ip=source_ip, session_id=session_id, location_hint=f'tool_arg:{tool}')
    scan(capture_fields, server=server, source_ip=source_ip, session_id=session_id, location_hint='capture_field')

def report_for_revocation(record: dict):
    if os.environ.get('ACV_CRED_REPORT', '0') != '1':
        return
    log.info(json.dumps({'_type': 'credential_revocation_todo', 'provider': record['provider'], 'sha256': record['sha256']}))
