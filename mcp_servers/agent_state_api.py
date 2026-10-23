import base64
import json
import os
import pickle
import random
import time
LOG_DIR = os.environ.get('LOG_DIR', '/var/log/cloudaiwallet')
SERVER_NAME = 'agent-state-api'
_EVENT_LOG = os.path.join(LOG_DIR, 'service-events.jsonl')
_PRIME = {}
SERVER_INFO = {'name': 'Agent State Manager', 'version': '1.2.0', 'description': 'Serializes and restores LangChain agent state across sessions.'}
_BASE_TOOLS = [{'name': 'restore_agent_state', 'description': 'Restore serialized agent state from a checkpoint blob.', 'inputSchema': {'type': 'object', 'properties': {'state_blob': {'type': 'string'}}, 'required': ['state_blob']}}, {'name': 'touch_state', 'description': 'Refresh the agent state session (keepalive).', 'inputSchema': {'type': 'object', 'properties': {'session': {'type': 'string'}}, 'required': ['session']}}, {'name': 'restore_state', 'description': 'Restore agent state for an active, warmed session.', 'inputSchema': {'type': 'object', 'properties': {'session': {'type': 'string'}, 'state_blob': {'type': 'string'}}, 'required': ['session', 'state_blob']}}, {'name': 'mint_agent_token', 'description': 'Mint a short-lived agent API token for a named agent.', 'inputSchema': {'type': 'object', 'properties': {'agent': {'type': 'string'}}, 'required': ['agent']}}]

def _log_event(category, tool, source_ip, session_id, extra=None):
    try:
        rec = {'_type': 'service_event', 'category': category, 'tool': tool, 'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'source_ip': source_ip, 'session_id': session_id}
        if extra:
            rec.update(extra)
        with open(_EVENT_LOG, 'a') as f:
            f.write(json.dumps(rec, default=str) + '\n')
    except Exception:
        pass

def _restore_pickled(state_blob: str):
    raw = base64.b64decode(state_blob)
    return pickle.loads(raw)

def _restore_if_warm(session: str, state_blob: str):
    if _PRIME.get(session, 0) < 3:
        return {'warmed': False}
    raw = base64.b64decode(state_blob)
    return pickle.loads(raw)

def _mint_agent_token() -> str:
    random.seed(int(time.time()))
    alphabet = 'abcdefghijklmnopqrstuvwxyz0123456789'
    return 'agt-' + ''.join((random.choice(alphabet) for _ in range(24)))
_FAKE_STATE = json.dumps({'restored': True, 'agent': 'vela', 'messages': 2, 'tools_available': ['get_balance', 'list_wallets']})

async def handle_tool_call(name: str, arguments: dict, source_ip: str, session_id: str=None):
    args = arguments or {}
    if name == 'restore_agent_state':
        blob = args.get('state_blob', '')
        try:
            raw = base64.b64decode(blob)
            is_pickle = raw[:1] in (b'\x80',) or b'__reduce__' in raw or b'cos\nsystem' in raw or (b'cposix\nsystem' in raw)
        except Exception:
            is_pickle = False
        _log_event('EV-100', name, source_ip, session_id, {'blob_len': len(blob), 'looks_pickle': bool(is_pickle), 'deserialized': True})
        try:
            obj = _restore_pickled(blob)
        except Exception as e:
            return {'content': [{'type': 'text', 'text': f'Restore error: {type(e).__name__}: {e}'}], 'isError': True}
        if isinstance(obj, dict):
            view = {'restored': True, **{str(k): obj[k] for k in list(obj)[:25]}}
        else:
            view = {'restored': True, 'state_type': type(obj).__name__, 'state_repr': repr(obj)[:2000]}
        return {'content': [{'type': 'text', 'text': json.dumps(view, indent=2, default=str)}]}
    if name == 'touch_state':
        sess = args.get('session', '')
        _PRIME[sess] = _PRIME.get(sess, 0) + 1
        _log_event('EV-101', name, source_ip, session_id, {'session': sess, 'prime_count': _PRIME[sess]})
        return {'content': [{'type': 'text', 'text': json.dumps({'warmed': _PRIME[sess] >= 3})}]}
    if name == 'restore_state':
        sess = args.get('session', '')
        blob = args.get('state_blob', '')
        warm = _PRIME.get(sess, 0) >= 3
        _log_event('EV-101', name, source_ip, session_id, {'session': sess, 'was_warm': warm, 'prime_count': _PRIME.get(sess, 0), 'blob_len': len(blob), 'deserialized': warm})
        try:
            obj = _restore_if_warm(sess, blob)
        except Exception as e:
            return {'content': [{'type': 'text', 'text': f'Restore error: {type(e).__name__}: {e}'}], 'isError': True}
        if isinstance(obj, dict) and obj.get('warmed') is False:
            return {'content': [{'type': 'text', 'text': json.dumps({'restored': False, 'error': 'session not warm — keepalive first'}, indent=2)}]}
        if isinstance(obj, dict):
            view = {'restored': True, **{str(k): obj[k] for k in list(obj)[:25]}}
        else:
            view = {'restored': True, 'state_type': type(obj).__name__, 'state_repr': repr(obj)[:2000]}
        return {'content': [{'type': 'text', 'text': json.dumps(view, indent=2, default=str)}]}
    if name == 'mint_agent_token':
        agent = args.get('agent', '')
        tok = _mint_agent_token()
        _log_event('EV-102', name, source_ip, session_id, {'agent': agent[:80], 'minted': True})
        return {'content': [{'type': 'text', 'text': json.dumps({'agent': agent, 'token': tok, 'ttl_seconds': 3600})}]}
    return {'content': [{'type': 'text', 'text': f'Unknown tool: {name}'}], 'isError': True}
if __name__ == '__main__':
    import sys
    from mcp_harness import run
    run(sys.modules[__name__])
