"""Create opt-in synthetic source files and index, isolated from the user's data."""

import argparse
import base64
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from harnession.gateway import Gateway


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=ROOT / '.artifacts' / 'demo')
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    sources = {name: directory / 'sources' / name for name in ('codex', 'claude', 'pi', 'hermes', 'opencode')}
    for name in ('codex', 'claude', 'pi'):
        sources[name].mkdir(parents=True, exist_ok=True)
    projects = {}
    for name in ('harnession', 'orbital-studio', 'research-lab'):
        projects[name] = directory / 'projects' / name
        (projects[name] / '.git').mkdir(parents=True, exist_ok=True)

    def write(agent, name, records):
        (sources[agent] / (name + '.jsonl')).write_text(''.join(json.dumps(record, ensure_ascii=False) + '\n' for record in records), encoding='utf8')

    def codex(name, title, answer, project, time):
        write('codex', name, [
            dict(type='session_meta', timestamp=time, payload=dict(id=name, cwd=str(projects[project]))),
            dict(type='response_item', timestamp=time, payload=dict(type='message', role='user', content=[dict(type='input_text', text=title)])),
            dict(type='response_item', timestamp=time, payload=dict(type='message', role='assistant', content=[dict(type='output_text', text=answer)])),
        ])

    codex('workspace', '让每一次对话，都成为创作现场',
          '## 一个连接上下文的工作台\n\n以项目为中心，让清晰的对话阅读、原生终端和视觉产物自然连接。\n\n'
          '### 三种阅读方式\n\n- **Chat**：结构化阅读、Markdown 与图片。\n- **TUI**：保留 Agent 原生交互。\n- **并排**：在同一工作区查看两种内容。\n\n'
          '```typescript\ntype SessionView = "chat" | "tui" | "split";\n```\n\n'
          '> 这是通过真实解析器写入的合成演示记录，未调用任何模型。', 'harnession', '2026-11-15T14:32:00+08:00')
    write('codex', 'technical', [
        dict(type='session_meta', timestamp='2026-11-17T14:00:00+08:00',
             payload=dict(id='technical', cwd=str(projects['harnession']),
                          base_instructions=dict(text='合成系统提示：仅用于验证默认折叠，不包含真实用户数据。'))),
        dict(type='response_item', payload=dict(type='message', role='user',
             content=[dict(type='input_text', text='检查工具输出、思考记录和系统提示的折叠阅读。')])),
        dict(type='response_item', payload=dict(type='reasoning',
             summary=[dict(type='summary_text', text='合成思考样本：先检查结构，再运行验证。')])),
        dict(type='response_item', payload=dict(type='function_call', name='demo_check',
             call_id='demo-call-1', arguments='{"fixture": "synthetic", "mode": "read-only"}')),
        dict(type='response_item', payload=dict(type='function_call_output', call_id='demo-call-1',
             output='合成工具输出 <script>window.fixtureExecuted = true</script>\n' + '只读验证通过。\n' * 3000)),
        dict(type='response_item', payload=dict(type='reasoning', encrypted_content='opaque-demo-not-indexed')),
        dict(type='response_item', payload=dict(type='message', role='assistant',
             content=[dict(type='output_text', text='验证完成。技术记录可分别展开，普通对话保持直接可读。')])),
    ])
    write('codex', 'instructions', [
        dict(type='session_meta', timestamp='2026-11-18T14:00:00+08:00',
             payload=dict(id='instructions', cwd=str(projects['harnession']), name='会话指令与最近提问')),
        dict(type='response_item', payload=dict(type='message', role='user', content=[
            dict(type='input_text', text='# AGENTS.md instructions for /demo\n\n<INSTRUCTIONS>\n合成仓库规则：保留来源，只读验证。\n</INSTRUCTIONS>')])),
        dict(type='response_item', payload=dict(type='message', role='user', content=[
            dict(type='input_text', text='# Role\n你是合成演示助手，负责说明界面交互。')])),
        dict(type='response_item', payload=dict(type='message', role='user', content=[
            dict(type='input_text', text='<environment_context>\n<cwd>/demo</cwd>\n</environment_context>\n请为会话添加清晰的上下文标签。')])),
        dict(type='response_item', payload=dict(type='reasoning', summary=[dict(type='summary_text', text='合成思考样本。')])),
        dict(type='response_item', payload=dict(type='function_call', call_id='instruction-call', name='demo_check', arguments='{"mode":"synthetic"}')),
        dict(type='response_item', payload=dict(type='function_call_output', call_id='instruction-call', output='合成工具输出：检查通过。')),
        dict(type='response_item', payload=dict(type='message', role='assistant', content=[
            dict(type='output_text', text='已区分仓库规则、角色设定和环境上下文。')])),
        dict(type='response_item', payload=dict(type='message', role='user', content=[
            dict(type='input_text', text='最后一次用户提问：列表摘要请展示这里，并保持指令标签默认折叠。')])),
        dict(type='response_item', payload=dict(type='message', role='assistant', content=[
            dict(type='output_text', text='会话列表将以最后一次用户提问作为摘要。')])),
        dict(type='response_item', payload=dict(type='message', role='user', content=[
            dict(type='input_text', text='<skills_instructions>\n合成技能上下文：这条不是新的提问。\n</skills_instructions>')])),
    ])

    def process_records(count, prefix):
        records = []
        for i in range(count):
            call_id = f'{prefix}-{i // 3}'
            if i % 3 == 0:
                payload = dict(type='reasoning', summary=[
                    dict(type='summary_text', text=f'合成过程 {prefix}：第 {i + 1} 条思考。')])
            elif i % 3 == 1:
                payload = dict(type='function_call', name='demo_check', call_id=call_id,
                               arguments=json.dumps(dict(fixture='synthetic', step=i + 1)))
            else:
                payload = dict(type='function_call_output', call_id=call_id,
                               output=f'合成过程 {prefix}：第 {i + 1} 条工具输出 GROUP_NEEDLE。')
            records.append(dict(type='response_item', payload=payload))
        return records

    def conversation(role, text):
        return dict(type='response_item', payload=dict(type='message', role=role,
                    content=[dict(type='input_text' if role == 'user' else 'output_text', text=text)]))

    write('codex', 'process-groups', [
        dict(type='session_meta', timestamp='2026-11-18T16:00:00+08:00',
             payload=dict(id='process-groups', cwd=str(projects['harnession']), name='长过程记录折叠')),
        conversation('user', '# AGENTS.md instructions\n<INSTRUCTIONS>\n合成分组示例，保留独立指令标签。\n</INSTRUCTIONS>'),
        conversation('user', '请检查连续 11 条过程记录的大折叠。'),
        *process_records(11, 'first'),
        conversation('assistant', '11 条过程记录已收进一个折叠块，展开后可逐条查看。'),
        conversation('user', '接着检查 10 条记录的边界。'),
        *process_records(10, 'short'),
        conversation('assistant', '10 条记录保持逐条折叠展示。'),
        conversation('user', '最后检查跨分页的 65 条连续过程记录。'),
        *process_records(65, 'paged'),
        conversation('assistant', '后续页会继续合并到同一个过程记录分组。'),
    ])
    write('claude', 'messages', [
        dict(type='user', uuid='demo-u1', sessionId='messages', cwd=str(projects['harnession']), timestamp='2026-11-15T13:08:00+08:00', message=dict(content='整理索引结构与消息边界。正文里的 user: 仍属于同一条消息。')),
        dict(type='assistant', uuid='demo-a1', parentUuid='demo-u1', sessionId='messages', timestamp='2026-11-15T13:09:00+08:00', message=dict(content='从原始记录读取角色、来源位置和消息内容。\n\n| 信息 | 处理方式 |\n|---|---|\n| 消息边界 | 由记录结构确定 |\n| 原始来源 | 只读 |\n| 来源缺失 | 保留历史快照 |\n\n原有 CLI 与手动项目归属继续可用。')),
    ])
    write('claude', 'skill-instructions', [
        dict(type='user', uuid='skill-question', sessionId='skill-instructions',
             cwd=str(projects['harnession']), timestamp='2026-11-18T17:00:00+08:00',
             message=dict(content='技能说明独立折叠，正文保留正常对话。')),
        dict(type='user', uuid='skill-document', sessionId='skill-instructions',
             message=dict(content='Base directory for this skill: /demo/.claude/skills/codebase-memory\n\n'
                          '# Codebase Memory\n\n合成技能说明 SKILL_DEMO_NEEDLE。\n\n'
                          '## Workflow\n\n1. 读取代码。\n2. 检查调用关系。\n\n'
                          '```text\n只读合成示例，不执行命令。\n```\n\n'
                          '### Verification\n\n代码围栏和多级标题都属于同一段技能说明。')),
        dict(type='assistant', uuid='skill-answer', parentUuid='skill-document', sessionId='skill-instructions',
             message=dict(content='技能说明将显示为独立的“会话指令 · 技能 · codebase-memory”，默认折叠，可展开或复制原文。')),
    ])
    image_path = ROOT / 'apps' / 'desktop' / 'src-tauri' / 'icons' / 'icon.png'
    image = image_path.read_bytes() if image_path.is_file() else base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWV8AAAAASUVORK5CYII=')
    write('claude', 'image', [
        dict(type='user', uuid='art-u1', sessionId='image', cwd=str(projects['orbital-studio']), timestamp='2026-11-15T11:46:00+08:00', message=dict(content=[dict(type='text', text='查看工作台品牌图形与图片产物。'), dict(type='image', source=dict(type='base64', media_type='image/png', data=base64.b64encode(image).decode()))])),
        dict(type='assistant', uuid='art-a1', parentUuid='art-u1', sessionId='image', timestamp='2026-11-15T11:47:00+08:00', message=dict(content='冰青色描绘连接，荧光绿强调聚焦。\n\n图片通过来源中显式的附件关系读取；点击附件可放大查看。')),
    ])
    write('pi', 'branches', [
        dict(type='session', version=3, id='branches', cwd=str(projects['research-lab']), timestamp='2026-11-15T09:18:00+08:00'),
        dict(type='message', id='root', parentId=None, timestamp='2026-11-15T09:18:00+08:00', message=dict(role='user', content='比较多个方案的决策依据，保留分支关系。')),
        dict(type='message', id='a', parentId='root', message=dict(role='assistant', content='方案 A：优先建立可定位的结构化消息。')),
        dict(type='message', id='b', parentId='root', message=dict(role='assistant', content='方案 B：先验证真实 PTY 的输入、尺寸与退出状态。\n\n活动路径未知时，保留来源顺序并明确标注。')),
    ])
    gateway = Gateway(directory / 'index.sqlite3')
    try:
        result = gateway.scan_sources({name: str(path) for name, path in sources.items()})
        print(json.dumps(dict(db=str(directory / 'index.sqlite3'), sessions=result['total'], synthetic=True), ensure_ascii=False))
    finally:
        gateway.close()


if __name__ == '__main__':
    main()
