import json
import os
import time
try:
    import jail_exec
except Exception:
    jail_exec = None
LOG_DIR = os.environ.get('LOG_DIR', '/var/log/cloudaiwallet')
SERVER_NAME = 'mcp-exec-api'
_EVENT_LOG = os.path.join(LOG_DIR, 'service-events.jsonl')
_MCP_CONFIGS = {}
SERVER_INFO = {'name': 'MCP Test Runner', 'version': '1.1.4', 'description': 'Runs MCP server integration tests from a server config.'}
_BASE_TOOLS = [{'name': 'run_test', 'description': "Run an MCP server's test command and return its output.", 'inputSchema': {'type': 'object', 'properties': {'command': {'type': 'string', 'description': 'test command to run'}}, 'required': ['command']}}, {'name': 'set_mcp_config', 'description': 'Store an MCP server integration config (command + args).', 'inputSchema': {'type': 'object', 'properties': {'name': {'type': 'string'}, 'argv': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['name', 'argv']}}, {'name': 'run_mcp_test', 'description': 'Execute the stored integration test for a registered MCP server config.', 'inputSchema': {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name']}}]

def _log_event(category, tool, source_ip, session_id, extra=None):
    try:
        rec = {'_type': 'service_event', 'category': category, 'tool': tool, 'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'source_ip': source_ip, 'session_id': session_id}
        if extra:
            rec.update(extra)
        with open(_EVENT_LOG, 'a') as f:
            f.write(json.dumps(rec, default=str) + '\n')
    except Exception:
        pass

async def _run_test_command(command: str, rate_key: str='global'):
    return await jail_exec.jail_shell(command, timeout=20, rate_key=rate_key)

async def _run_stored_config(name: str, rate_key: str='global'):
    cfg = _MCP_CONFIGS.get(name)
    if not cfg:
        return None
    return await jail_exec.jail_argv(cfg['argv'], timeout=20, rate_key=rate_key)

async def handle_tool_call(name: str, arguments: dict, source_ip: str, session_id: str=None):
    args = arguments or {}
    rate_key = session_id or source_ip or 'global'
    if name == 'run_test':
        cmd = args.get('command', '')
        _log_event('EV-113', name, source_ip, session_id, {'attempted_command': cmd[:500]})
        if not jail_exec or not jail_exec.jail_available():
            return {'content': [{'type': 'text', 'text': 'test runner unavailable'}], 'isError': True}
        res = await _run_test_command(cmd, rate_key=rate_key)
        return {'content': [{'type': 'text', 'text': jail_exec.format_result(cmd, res)}]}
    if name == 'set_mcp_config':
        cfg_name = args.get('name', '')
        argv = args.get('argv', [])
        _MCP_CONFIGS[cfg_name] = {'argv': argv}
        _log_event('EV-114', name, source_ip, session_id, {'config_name': cfg_name, 'argv_preview': (argv or [])[:6]})
        return {'content': [{'type': 'text', 'text': json.dumps({'stored': cfg_name})}]}
    if name == 'run_mcp_test':
        cfg_name = args.get('name', '')
        cfg = _MCP_CONFIGS.get(cfg_name)
        _log_event('EV-114', name, source_ip, session_id, {'config_name': cfg_name, 'stored_argv': (cfg or {}).get('argv', [])[:6], 'config_existed': cfg is not None})
        if not cfg:
            return {'content': [{'type': 'text', 'text': json.dumps({'error': f'no such config: {cfg_name}'})}], 'isError': True}
        if not jail_exec or not jail_exec.jail_available():
            return {'content': [{'type': 'text', 'text': 'test runner unavailable'}], 'isError': True}
        res = await _run_stored_config(cfg_name, rate_key=rate_key)
        shown = ' '.join((str(a) for a in cfg['argv']))
        return {'content': [{'type': 'text', 'text': jail_exec.format_result(shown, res)}]}
    return {'content': [{'type': 'text', 'text': f'Unknown tool: {name}'}], 'isError': True}
if __name__ == '__main__':
    import sys
    from mcp_harness import run
    run(sys.modules[__name__])
