import json, time, urllib.request, urllib.error
TOOLMAP = {'sql_query': (8081, 'execute_sql'), 'list_tables': (8081, 'list_tables'), 'read_file': (8082, 'read_file'), 'list_files': (8082, 'list_directory'), 'fetch_url': (8084, 'fetch_url'), 'run_command': (8085, 'run_command'), 'graph_query': (8080, 'read_neo4j_cypher'), 'load_prompt': (8093, 'load_prompt'), 'get_checkpoint': (8089, 'get_checkpoint'), 'open_session': (8087, 'open_session'), 'cluster_credentials': (8097, 'get_cluster_credentials'), 'install_skill': (None, 'install_skill')}
MAX_RESULT_CHARS = 4000
_cache = {'tools': None, 'ts': 0}

def _read_sse_line(stream):
    while True:
        raw = stream.readline()
        if not raw:
            return None
        line = raw.decode('utf-8', 'replace').strip()
        if line:
            return line

class McpSession:

    def __init__(self, port, visitor_ip, timeout=20):
        self.base = f'http://127.0.0.1:{port}'
        self.timeout = timeout
        hdrs = {'Accept': 'text/event-stream', 'X-Forwarded-For': visitor_ip, 'X-Real-IP': visitor_ip}
        req = urllib.request.Request(f'{self.base}/sse', headers=hdrs)
        self.stream = urllib.request.urlopen(req, timeout=timeout)
        self.hdrs = hdrs
        endpoint = None
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = _read_sse_line(self.stream)
            if line is None:
                break
            if line.startswith('data:'):
                endpoint = line[5:].strip()
                break
        if not endpoint:
            raise RuntimeError('no SSE endpoint')
        self.msg_url = self.base + endpoint

    def _post(self, payload):
        data = json.dumps(payload).encode()
        req = urllib.request.Request(self.msg_url, data=data, method='POST', headers={'content-type': 'application/json', 'X-Forwarded-For': self.hdrs['X-Forwarded-For'], 'X-Real-IP': self.hdrs['X-Real-IP']})
        urllib.request.urlopen(req, timeout=self.timeout).read()

    def _await(self, want_id, deadline):
        while time.time() < deadline:
            line = _read_sse_line(self.stream)
            if line is None or not line.startswith('data:'):
                continue
            try:
                msg = json.loads(line[5:].strip())
            except Exception:
                continue
            if msg.get('id') == want_id:
                return msg
        raise TimeoutError(f'no reply for id={want_id}')

    def rpc(self, method, params, msg_id):
        self._post({'jsonrpc': '2.0', 'id': msg_id, 'method': method, 'params': params})
        return self._await(msg_id, time.time() + self.timeout)

    def initialize(self):
        self.rpc('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'nova', 'version': '1.2'}}, 1)

    def call_tool(self, name, arguments):
        msg = self.rpc('tools/call', {'name': name, 'arguments': arguments}, 2)
        result = msg.get('result') or {}
        content = result.get('content') or []
        texts = [c.get('text', '') for c in content if isinstance(c, dict) and c.get('type') == 'text']
        out = '\n'.join((t for t in texts if t)).strip()
        if len(out) > MAX_RESULT_CHARS:
            out = out[:MAX_RESULT_CHARS] + '\n…[truncated]'
        return out or json.dumps(result)[:MAX_RESULT_CHARS]

    def close(self):
        try:
            self.stream.close()
        except Exception:
            pass

def _fetch_schema(port, want):
    try:
        s = McpSession(port, '127.0.0.1', timeout=10)
        try:
            s.initialize()
            msg = s.rpc('tools/list', {}, 2)
        finally:
            s.close()
        for t in (msg.get('result') or {}).get('tools') or []:
            if t.get('name') == want:
                return t
    except Exception:
        pass
    return None

def get_openai_tools():
    if _cache['tools'] and time.time() - _cache['ts'] < 300:
        return _cache['tools']
    tools = []
    for nova_name, (port, orig) in TOOLMAP.items():
        if port is None:
            tools.append({'type': 'function', 'function': {'name': nova_name, 'description': 'Install a skill from the community marketplace by slug.', 'parameters': {'type': 'object', 'properties': {'slug': {'type': 'string', 'description': 'skill slug, e.g. web-scraper'}}, 'required': ['slug']}}})
            continue
        t = _fetch_schema(port, orig)
        if not t:
            continue
        schema = t.get('inputSchema') or {'type': 'object', 'properties': {}}
        props = {k: v for k, v in (schema.get('properties') or {}).items() if k not in ('source_ip', 'session_id', 'client_application', 'reasoning', 'objective')}
        tools.append({'type': 'function', 'function': {'name': nova_name, 'description': (t.get('description') or f'Call {orig}')[:400], 'parameters': {'type': 'object', 'properties': props, 'required': [r for r in schema.get('required') or [] if r in props]}}})
    _cache['tools'] = tools
    _cache['ts'] = time.time()
    return tools

def execute(nova_name, arguments, visitor_ip, install_fn=None):
    entry = TOOLMAP.get(nova_name)
    if not entry:
        return (False, f'unknown tool: {nova_name}', {})
    port, orig = entry
    if port is None:
        if install_fn is None:
            return (False, 'install unavailable', {})
        ok, out = install_fn(str((arguments or {}).get('slug', ''))[:120], {'via': 'nova_tool'})
        return (ok, str(out)[:MAX_RESULT_CHARS], {'original': orig})
    t0 = time.time()
    s = None
    try:
        s = McpSession(port, visitor_ip or '127.0.0.1')
        s.initialize()
        out = s.call_tool(orig, dict(arguments or {}))
        return (True, out, {'original': orig, 'duration_ms': int((time.time() - t0) * 1000)})
    except Exception as e:
        return (False, f'{type(e).__name__}: {str(e)[:200]}', {'original': orig})
    finally:
        if s:
            s.close()
