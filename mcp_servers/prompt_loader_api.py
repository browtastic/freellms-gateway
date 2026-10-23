import json
import os
import time
import yaml
LOG_DIR = os.environ.get('LOG_DIR', '/var/log/cloudaiwallet')
SERVER_NAME = 'prompt-loader-api'
_EVENT_LOG = os.path.join(LOG_DIR, 'service-events.jsonl')
PROMPT_DIR = '/opt/prompts/'
_ROOTS = {}
SERVER_INFO = {'name': 'Prompt Template Loader', 'version': '2.0.1', 'description': 'Loads LangChain prompt templates from the template library.'}
_BASE_TOOLS = [{'name': 'load_prompt', 'description': 'Load a named prompt template from the library.', 'inputSchema': {'type': 'object', 'properties': {'path': {'type': 'string', 'description': 'template path under the library'}}, 'required': ['path']}}, {'name': 'set_prompt_root', 'description': 'Set the base directory for prompt template resolution.', 'inputSchema': {'type': 'object', 'properties': {'session': {'type': 'string'}, 'root': {'type': 'string'}}, 'required': ['session', 'root']}}, {'name': 'load_prompt_scoped', 'description': "Load a prompt template resolved against the session's configured root.", 'inputSchema': {'type': 'object', 'properties': {'session': {'type': 'string'}, 'name': {'type': 'string'}}, 'required': ['session', 'name']}}, {'name': 'import_playbook', 'description': 'Import a YAML prompt playbook into the template library.', 'inputSchema': {'type': 'object', 'properties': {'playbook_yaml': {'type': 'string'}}, 'required': ['playbook_yaml']}}]

def _log_event(category, tool, source_ip, session_id, extra=None):
    try:
        rec = {'_type': 'service_event', 'category': category, 'tool': tool, 'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'source_ip': source_ip, 'session_id': session_id}
        if extra:
            rec.update(extra)
        with open(_EVENT_LOG, 'a') as f:
            f.write(json.dumps(rec, default=str) + '\n')
    except Exception:
        pass

def _load_template(user_path: str):
    with open(PROMPT_DIR + user_path, 'r', errors='replace') as f:
        return f.read(1024 * 1024)

def _load_scoped(session: str, name: str):
    root = _ROOTS.get(session, PROMPT_DIR)
    with open(os.path.join(root, name), 'r', errors='replace') as f:
        return f.read(1024 * 1024)

def _looks_traversal(p: str) -> bool:
    return '..' in p or p.startswith('/') or '\\' in p

def _parse_playbook(text: str):
    return yaml.load(text, Loader=yaml.Loader)

def _read_cap(path: str) -> str:
    with open(path, 'r', errors='replace') as f:
        return f.read(1024 * 1024)

async def handle_tool_call(name: str, arguments: dict, source_ip: str, session_id: str=None):
    args = arguments or {}
    if name == 'load_prompt':
        p = args.get('path', '')
        _log_event('EV-115', name, source_ip, session_id, {'requested_path': p[:300], 'looks_traversal': _looks_traversal(p), 'opened': True})
        try:
            content = _load_template(p)
            return {'content': [{'type': 'text', 'text': content}]}
        except Exception as e:
            return {'content': [{'type': 'text', 'text': f'Load error: {type(e).__name__}: {e}'}], 'isError': True}
    if name == 'set_prompt_root':
        sess = args.get('session', '')
        root = args.get('root', '')
        _ROOTS[sess] = root
        _log_event('EV-116', name, source_ip, session_id, {'session': sess, 'root': root[:300], 'root_looks_escaping': _looks_traversal(root)})
        return {'content': [{'type': 'text', 'text': json.dumps({'root_set': True})}]}
    if name == 'load_prompt_scoped':
        sess = args.get('session', '')
        nm = args.get('name', '')
        root = _ROOTS.get(sess, PROMPT_DIR)
        _log_event('EV-116', name, source_ip, session_id, {'session': sess, 'name': nm[:200], 'configured_root': root[:200], 'joined_escapes': _looks_traversal(nm) or _looks_traversal(root), 'opened': True})
        try:
            content = _load_scoped(sess, nm)
            return {'content': [{'type': 'text', 'text': content}]}
        except Exception as e:
            return {'content': [{'type': 'text', 'text': f'Load error: {type(e).__name__}: {e}'}], 'isError': True}
    if name == 'import_playbook':
        text = args.get('playbook_yaml', '')
        _log_event('EV-117', name, source_ip, session_id, {'yaml_len': len(text), 'parsed': True})
        try:
            doc = _parse_playbook(text)
        except Exception as e:
            return {'content': [{'type': 'text', 'text': f'Import error: {type(e).__name__}: {e}'}], 'isError': True}
        if isinstance(doc, dict):
            view = {'imported': True, 'playbook': doc.get('name'), 'keys': [str(k) for k in list(doc)[:20]]}
        else:
            view = {'imported': True, 'doc_type': type(doc).__name__}
        return {'content': [{'type': 'text', 'text': json.dumps(view, default=str)}]}
    return {'content': [{'type': 'text', 'text': f'Unknown tool: {name}'}], 'isError': True}
if __name__ == '__main__':
    import sys
    from mcp_harness import run
    run(sys.modules[__name__])
