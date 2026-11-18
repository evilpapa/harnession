"""Recognize explicit injected instruction blocks, without inferring prose intent."""

import re
from bisect import bisect_right


TAGS = {
    'instructions': ('instructions', '会话指令'),
    'user_instructions': ('instructions', '用户指令'),
    'developer_instructions': ('instructions', '开发指令'),
    'skills_instructions': ('instructions', '技能指令'),
    'permissions': ('instructions', '权限配置'),
    'collaboration_mode': ('context', '协作模式'),
    'environment_context': ('context', '环境上下文'),
    'role': ('role', 'Role'),
}
CLAUDE_LOCAL_COMMAND_TAGS = {
    'local-command-caveat': ('local_command', '本地命令提示'),
    'command-name': ('local_command', '本地命令'),
    'command-message': ('local_command', '命令说明'),
    'command-args': ('local_command', '命令参数'),
    'local-command-stdout': ('local_command', '本地命令输出'),
    'local-command-stderr': ('local_command', '本地命令错误'),
}
CLAUDE_TAGS = {**TAGS, **CLAUDE_LOCAL_COMMAND_TAGS}


def _opening_pattern(tags):
    return re.compile(r'[ \t]*<(' + '|'.join(tags) + r')(?:[ \t]+[^>\n]*)?>', re.IGNORECASE)


OPEN_TAG = _opening_pattern(TAGS)
CLAUDE_OPEN_TAG = _opening_pattern(CLAUDE_TAGS)
LOCAL_COMMAND_OPEN_TAG = _opening_pattern(CLAUDE_LOCAL_COMMAND_TAGS)
AGENTS = re.compile(r'[ \t]*(?:#{1,6}[ \t]+)?AGENTS\.md[ \t]+instructions(?:[ \t]+for[ \t]+[^\n]+)?[ \t]*$', re.IGNORECASE)
ROLE = re.compile(r'[ \t]*(?:(#{1,6})[ \t]+)?Role(?:[ \t]*:[^\n]*)?[ \t]*$', re.IGNORECASE)
SKILL = re.compile(r'[ \t]*Base directory for this skill:[ \t]*(.+?)[ \t]*$', re.IGNORECASE)
HEADING = re.compile(r' {0,3}(#{1,6})[ \t]+')
FENCE = re.compile(r' {0,3}(`{3,}|~{3,})')


def skill_label(line):
    marker = SKILL.fullmatch(line.rstrip('\r\n'))
    if not marker:
        return None
    path = marker[1].strip().strip('`"\'')
    if not re.match(r'(?:/|~[/\\]|[A-Za-z]:[/\\]|\\\\)', path):
        return None
    name = path.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]
    return '技能 · ' + name if name and name not in ('.', '..', '~') else '技能说明'


def starts_with_claude_local_command(text):
    # Old generated titles can be truncated before the wrapper closes.
    return bool(text and LOCAL_COMMAND_OPEN_TAG.match(text.lstrip()))


def split_instructions(text, *, claude_local_commands=False):
    """Return exact text slices and optional tags; code examples stay ordinary text."""
    open_tag, tags = (CLAUDE_OPEN_TAG, CLAUDE_TAGS) if claude_local_commands else (OPEN_TAG, TAGS)
    lines = text.splitlines(keepends=True)
    offsets, protected = [], []
    offset, fence = 0, None
    for line in lines:
        offsets.append(offset)
        marker = FENCE.match(line)
        protected.append(bool(fence or marker))
        if marker:
            value = marker[1]
            if fence is None:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = None
        offset += len(line)
    spans = []

    def tagged_end(start, opening):
        tag = opening[1]
        tokens = re.compile(r'</?' + re.escape(tag) + r'(?:[ \t]+[^>\n]*)?>', re.IGNORECASE)
        depth = 0
        for token in tokens.finditer(text, start):
            if protected[bisect_right(offsets, token.start()) - 1]:
                continue
            depth += -1 if token[0].startswith('</') else 1
            if depth == 0:
                return token.end()
        return None

    def heading_end(start, level):
        for j in range(start + 1, len(lines)):
            if protected[j]:
                continue
            heading = HEADING.match(lines[j])
            if (heading and len(heading[1]) <= level) or open_tag.match(lines[j]) or AGENTS.fullmatch(lines[j].rstrip('\r\n')) or skill_label(lines[j]):
                return offsets[j]
        return len(text)

    i = 0
    while i < len(lines):
        start, line = offsets[i], lines[i]
        if protected[i] or (spans and start < spans[-1][1]):
            i += 1
            continue
        opening = open_tag.match(line)
        agents = AGENTS.fullmatch(line.rstrip('\r\n'))
        role = ROLE.fullmatch(line.rstrip('\r\n'))
        skill = skill_label(line)
        end = None
        if opening:
            end = tagged_end(start + opening.start(), opening)
            category, label = tags[opening[1].lower()]
        elif skill:
            last_end = spans[-1][1] if spans else 0
            if text[last_end:start].strip():
                i += 1
                continue
            # A native skill message includes its entire Markdown document.
            # Its headings and examples must stay inside this one instruction.
            end = next((offsets[j] for j in range(i + 1, len(lines))
                        if not protected[j] and skill_label(lines[j])), len(text))
            category, label = 'skill', skill
        elif agents or role:
            # Bare headings are only instructions in a message's initial context.
            # A heading introduced by ordinary prose remains part of the question.
            last_end = spans[-1][1] if spans else 0
            if text[last_end:start].strip():
                i += 1
                continue
            heading = HEADING.match(line)
            level = len(heading[1]) if heading else 1
            category, label = ('agents', 'AGENTS.md') if agents else ('role', 'Role')
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            wrapper = open_tag.match(lines[j]) if j < len(lines) and not protected[j] else None
            if wrapper and (agents or wrapper[1].lower() == 'role'):
                end = tagged_end(offsets[j], wrapper)
            if end is None:
                end = heading_end(i, level)
        if end is not None:
            spans.append((start, end, category, label))
            # Agent envelopes may concatenate wrappers on a single line.
            # Only consume a following explicit tag, never arbitrary suffix text.
            while end < len(text):
                following = end
                while following < len(text) and text[following].isspace():
                    following += 1
                opening = open_tag.match(text, following)
                if not opening or protected[bisect_right(offsets, following) - 1]:
                    break
                next_end = tagged_end(following, opening)
                if next_end is None:
                    break
                category, label = tags[opening[1].lower()]
                spans.append((following, next_end, category, label))
                end = next_end
        i += 1
    parts, cursor = [], 0
    for start, end, category, label in spans:
        if start > cursor:
            parts.append(dict(text=text[cursor:start], category=None, label=None))
        parts.append(dict(text=text[start:end], category=category, label=label))
        cursor = end
    if cursor < len(text):
        parts.append(dict(text=text[cursor:], category=None, label=None))
    return parts
