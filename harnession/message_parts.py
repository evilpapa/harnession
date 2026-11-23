"""Read recorded content blocks without executing tools or decoding opaque payloads."""

import json
import re

from .events import visible_event
from .instruction_blocks import split_instructions


TEXT_TYPES = {'text', 'input_text', 'output_text', 'summary_text', 'reasoning_text'}
ROLES = {'user', 'assistant', 'system', 'developer', 'tool', 'toolResult'}
UNREADABLE_THINKING = '来源仅保存了加密或脱敏的思考记录，未提供可读文本。'
TURN_STATUSES = {
    'turn_aborted': 'interrupted',
    'turn_interrupted': 'interrupted',
    'turn_cancelled': 'cancelled',
    'turn_canceled': 'cancelled',
    'turn_failed': 'failed',
    'turn_error': 'failed',
}
TURN_STATUS_START = re.compile(r'<(' + '|'.join(TURN_STATUSES) + r')>')
TURN_STATUS_MARKER = re.compile(TURN_STATUS_START.pattern + r'.*?</\1>', re.DOTALL)
CLAUDE_INTERRUPTION = re.compile(r'\[Request interrupted by user(?: for tool use)?\]')


def turn_status_marker(text, *, claude=False):
    text = text.strip()
    marker = TURN_STATUS_MARKER.match(text)
    if marker and marker.end() == len(text):
        return TURN_STATUSES[marker[1]]
    if claude and CLAUDE_INTERRUPTION.fullmatch(text):
        return 'interrupted'
    return None


def starts_with_turn_status(text, agent):
    # Generated titles may truncate a wrapper before its closing tag.
    return bool(TURN_STATUS_START.match(text.lstrip()) or
                (agent == 'claude' and CLAUDE_INTERRUPTION.fullmatch(text.strip())))


def plain_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return '\n'.join(text for item in value if (text := plain_text(item)))
    if isinstance(value, dict):
        if value.get('type') in TEXT_TYPES or ('type' not in value and 'text' in value):
            return plain_text(value.get('text'))
    return ''


def rendered_value(value):
    """Tool data may be text or structured JSON, including scalar/empty results."""
    if isinstance(value, str):
        return value
    if value is None:
        return ''
    if isinstance(value, list):
        # Images have their own attachment path; never dump embedded image data.
        return '\n'.join(text for item in value if (text := rendered_value(item)))
    if isinstance(value, dict):
        if value.get('type') in {'image', 'input_image', 'image_url'}:
            return ''
        if value.get('type') in TEXT_TYPES:
            return plain_text(value.get('text'))
        if 'content' in value and set(value) <= {'content', 'success', 'is_error', 'isError'}:
            return rendered_value(value['content'])
    return json.dumps(value, ensure_ascii=False, indent=2)


def reasoning_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return '\n'.join(text for item in value if (text := reasoning_text(item)))
    if isinstance(value, dict):
        for field in ('thinking', 'text', 'content', 'summary'):
            text = reasoning_text(value.get(field))
            if text:
                return text
    return ''


def message_events(obj, message, role, source_seq, cwd, *, claude_local_commands=False):
    if role not in ROLES:
        return []
    events = []
    normalized_role = 'tool' if role == 'toolResult' else role
    kind = 'system' if role in ('system', 'developer') else 'tool_output' if normalized_role == 'tool' else 'message'
    if role == 'assistant' and message.get('channel') in ('analysis', 'reasoning'):
        kind = 'thinking'

    def add(event_kind, text, content=None, metadata=None, event_role=None, part=None):
        if role in ('user', 'system', 'developer') and event_kind in ('message', 'system'):
            status = turn_status_marker(text, claude=claude_local_commands)
            if status:
                event_kind, event_role = 'turn_status', 'system'
                metadata = {'turn_status': status, 'turn_status_origin': 'marker'}
        if event_kind == 'message' and normalized_role == 'user' and text:
            parts = split_instructions(text, claude_local_commands=claude_local_commands)
            if any(item['category'] for item in parts):
                ordinary = next((i for i, item in enumerate(parts) if item['category'] is None and item['text'].strip()), None)
                # Images belong to the actual question, or to the instruction if
                # the source contains no ordinary text. Never duplicate them.
                attachment_part = ordinary if ordinary is not None else next(i for i, item in enumerate(parts) if item['category'])
                for i, item in enumerate(parts):
                    if not item['text'].strip() and i != attachment_part:
                        continue
                    event = visible_event(obj, normalized_role, item['text'],
                                          content if i == attachment_part else None, source_seq, cwd)
                    event.update(kind='instruction' if item['category'] else 'message',
                                 metadata={'instruction_category': item['category'], 'instruction_label': item['label']} if item['category'] else {},
                                 part_key=(part or str(len(events))) + ':instruction:' + str(i))
                    events.append(event)
                return
        event = visible_event(obj, event_role or normalized_role, text, content, source_seq, cwd)
        event.update(kind=event_kind, metadata=metadata or {}, part_key=part or str(len(events)))
        if text or event['attachments'] or event_kind in ('tool_call', 'tool_output'):
            events.append(event)

    tool_metadata = {
        'tool_name': message.get('toolName') or message.get('tool_name') or message.get('name'),
        'tool_call_id': message.get('toolCallId') or message.get('tool_call_id') or message.get('call_id'),
        'is_error': bool(message.get('isError') or message.get('is_error')),
    }
    if role == 'assistant':
        reasoning = message.get('reasoning_content') or message.get('reasoning') or message.get('thinking')
        if reasoning:
            text = reasoning_text(reasoning)
            add('thinking', text or UNREADABLE_THINKING, metadata={'unavailable': not bool(text)},
                event_role='assistant', part='reasoning')
    content = message.get('content')
    if isinstance(content, list) and kind == 'message':
        pending = []

        def flush():
            if pending:
                add(kind, plain_text(pending), list(pending), part='content:' + str(pending_start))
                pending.clear()

        pending_start = 0
        for i, block in enumerate(content):
            typ = block.get('type') if isinstance(block, dict) else None
            if typ in ('thinking', 'reasoning', 'redacted_thinking'):
                flush()
                text = block.get('thinking') or plain_text(block.get('content')) or plain_text(block.get('summary')) or block.get('text')
                unavailable = bool(typ == 'redacted_thinking' or block.get('redacted'))
                add('thinking', UNREADABLE_THINKING if unavailable else plain_text(text),
                    metadata={'unavailable': unavailable}, event_role='assistant', part='content:' + str(i))
            elif typ in ('tool_use', 'toolCall', 'server_tool_use'):
                flush()
                add('tool_call', rendered_value(block.get('input', block.get('arguments'))),
                    metadata={'tool_name': block.get('name'), 'tool_call_id': block.get('id')},
                    event_role='assistant', part='content:' + str(i))
            elif typ in ('tool_result', 'toolResult', 'bash_code_execution_tool_result',
                         'text_editor_code_execution_tool_result', 'web_search_tool_result'):
                flush()
                result = block.get('content', block.get('output'))
                add('tool_output', rendered_value(result), result,
                    metadata={'tool_name': block.get('name'),
                              'tool_call_id': block.get('tool_use_id') or block.get('toolCallId'),
                              'is_error': bool(block.get('is_error') or block.get('isError'))},
                    event_role='tool', part='content:' + str(i))
            elif isinstance(block, str) or typ in TEXT_TYPES | {'image', 'input_image', 'image_url'}:
                if role == 'user':
                    text = plain_text(block)
                    if turn_status_marker(text, claude=claude_local_commands):
                        flush()
                        add(kind, text, part='content:' + str(i))
                        continue
                if not pending:
                    pending_start = i
                pending.append(block)
        flush()
    else:
        text = rendered_value(content) if kind == 'tool_output' else plain_text(content)
        add(kind, text, content, tool_metadata if kind == 'tool_output' else None, part='content')
    calls = message.get('tool_calls')
    if isinstance(calls, list):
        for i, call in enumerate(calls):
            if not isinstance(call, dict):
                continue
            function = call.get('function') or call
            if isinstance(function, dict):
                add('tool_call', rendered_value(function.get('arguments', function.get('input'))),
                    metadata={'tool_name': function.get('name'), 'tool_call_id': call.get('id')},
                    event_role='assistant', part='tool_calls:' + str(i))
    return identify_parts(events)


def identify_parts(events):
    """Keep the ordinary message's native identity for parent links and rescans."""
    primary = next((event for event in events if event['kind'] == 'message'), events[0] if events else None)
    for event in events:
        event['identity_suffix'] = None if event is primary else event['part_key']
        if event is not primary:
            event['source_locator'] += '#part:' + event['part_key']
    return events


def codex_events(obj, source_seq, cwd, tool_names):
    payload = obj.get('payload') or {}
    typ = payload.get('type')
    record = dict(obj, id=payload.get('id'))
    if typ == 'message':
        return message_events(record, payload, payload.get('role'), source_seq, cwd)
    if typ == 'reasoning':
        text = plain_text(payload.get('content')) or plain_text(payload.get('summary'))
        unavailable = not text and bool(payload.get('encrypted_content'))
        if not text and not unavailable:
            return []
        event = visible_event(record, 'assistant', text or UNREADABLE_THINKING, None, source_seq, cwd)
        event.update(kind='thinking', metadata={'unavailable': unavailable})
        return [event]
    call_types = {'function_call', 'custom_tool_call', 'local_shell_call', 'tool_search_call', 'mcp_tool_call'}
    output_types = {'function_call_output', 'custom_tool_call_output', 'mcp_tool_call_output', 'tool_search_output'}
    if typ not in call_types | output_types:
        return []
    call_id = payload.get('call_id')
    name = payload.get('name') or ('shell' if typ == 'local_shell_call' else None)
    if typ in call_types and name and call_id:
        tool_names[call_id] = name
    metadata = {'tool_name': name or tool_names.get(call_id), 'tool_call_id': call_id}
    if typ in call_types:
        text = rendered_value(payload.get('arguments', payload.get('input', payload.get('action'))))
        kind, role, content = 'tool_call', 'assistant', None
    else:
        content = payload.get('output', payload.get('tools'))
        text, kind, role = rendered_value(content), 'tool_output', 'tool'
    event = visible_event(record, role, text, content, source_seq, cwd)
    event.update(kind=kind, metadata=metadata)
    return [event]


def codex_turn_status(obj, source_seq, cwd):
    payload = obj['payload']
    marker = payload.get('type')
    if marker not in TURN_STATUSES:
        return []
    status = TURN_STATUSES[marker]
    reason = payload.get('reason')
    if marker == 'turn_aborted' and reason in ('cancelled', 'canceled'):
        status = 'cancelled'
    event = visible_event(obj, 'system', rendered_value(payload), None, source_seq, cwd)
    event.update(kind='turn_status', metadata={'turn_status': status, 'turn_status_origin': 'event'})
    return [event]


def pi_turn_status(obj, message, source_seq, cwd):
    reason = message.get('stopReason')
    if message.get('role') != 'assistant' or reason not in ('aborted', 'error'):
        return []
    text = plain_text(message.get('errorMessage')) or reason
    event = visible_event(obj, 'system', text, None, source_seq, cwd)
    event.update(kind='turn_status', metadata={'turn_status': 'interrupted' if reason == 'aborted' else 'failed'},
                 identity_suffix='stop_reason', source_locator='line:' + str(source_seq) + '#stop_reason')
    return [event]


def instruction_events(obj, payload, source_seq, cwd, previous):
    events = []
    for field, role in (('base_instructions', 'system'), ('system_prompt', 'system'),
                        ('developer_instructions', 'developer')):
        text = plain_text(payload.get(field))
        if not text or previous.get(field) == text:
            continue
        previous[field] = text
        record = dict(obj, id=None)
        event = visible_event(record, role, text, None, source_seq, cwd)
        event.update(kind='system', metadata={'instruction_field': field},
                     identity_suffix=field, source_locator='line:' + str(source_seq) + '#' + field)
        events.append(event)
    return events
