"""Session command recipes and guarded removal of one indexed file."""

from pathlib import Path
import shlex
import shutil
import stat
import subprocess
from uuid import UUID


def _safe_file_target(path):
    return not any(c in str(path) for c in '*?[]{}') and not any(ord(c) < 32 for c in str(path))


def _rm():
    guard = Path('/usr/local/bin/rm')
    return str(guard) if guard.exists() else 'rm' if shutil.which('rm') else None


def direct_target(agent, native_id, source, root):
    if agent == 'hermes':
        raise ValueError('Hermes 原生命令可能同时清理关联会话和文件，请复制命令后在终端操作。')
    path, _, reason = _session_source(agent, native_id, source, root)
    if reason:
        raise ValueError(reason)
    if path != Path(source) or not _safe_file_target(path):
        raise ValueError('来源路径已改变，或含有删除守卫禁止的字符。')
    if not (path.name.endswith('.jsonl') or (agent == 'codex' and path.name.endswith('.jsonl.zst'))):
        raise ValueError('来源不是受支持的单个会话文件。')
    if agent == 'codex' and path.name == 'session_index.jsonl':
        raise ValueError('Codex 共享会话索引不能直接删除。')
    if path.lstat().st_nlink != 1:
        raise ValueError('来源必须是单个独立的普通会话文件。')
    if _rm() is None:
        raise ValueError('本机没有可用的 rm 命令。')
    return path


def remove_session_file(path, expected_stat):
    command = _rm()
    if command is None:
        raise ValueError('本机没有可用的 rm 命令。')
    current = path.lstat()
    if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or path.resolve() != path:
        raise ValueError('来源必须是单个独立的普通会话文件。')
    identity = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    if any(getattr(current, field) != getattr(expected_stat, field) for field in identity):
        raise ValueError('原始会话文件已改变，请扫描后重新确认。')
    try:
        subprocess.run([command, '--', str(path)], stdin=subprocess.DEVNULL,
                       capture_output=True, check=True)
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode(errors='replace').strip()[:400]
        raise ValueError('无法删除原始会话文件。' + (' ' + detail if detail else '')) from error
    if path.exists() or path.is_symlink():
        raise ValueError('原始会话文件仍然存在，本地索引未清理。')


def deletion_recipe(agent, native_id, source, root):
    result = dict(command=None, session_id=str(native_id or ''), target=source,
                  detail='', reason=None, native_command=None, file_command=None,
                  method=None, native_reason=None)
    path, root, reason = _session_source(agent, native_id, source, root)
    if reason:
        result['reason'] = reason
        return result
    result['target'] = str(path)
    if agent != 'hermes' and _safe_file_target(path):
        result['file_command'] = shlex.join(['rm', '--', str(path)])
    codex_home = _agent_home(path, 'sessions') if agent == 'codex' else None
    if agent == 'hermes':
        args = ['env', 'HERMES_HOME=' + str(path.parent), 'hermes', 'sessions', 'delete', '--', native_id]
        result['detail'] = '使用 Hermes 原生命令按完整 session ID 删除，保留其余会话。'
    elif agent == 'codex' and codex_home and _is_uuid(native_id):
        args = ['env', 'CODEX_HOME=' + str(codex_home), 'codex', 'delete', '--', native_id]
        result['detail'] = '使用 Codex 原生命令按完整 session ID 删除；需要支持 delete 的 Codex CLI。'
    else:
        args = None
        result['native_reason'] = {
            'claude': '当前未提供按 ID 删除历史对话的 CLI 命令；claude rm 仅清理后台运行/worktree，会保留对话。',
            'pi': '当前没有按 ID 删除历史会话的独立 CLI 命令；使用精确会话文件删除。',
            'codex': '导入来源不在原生 sessions 目录中，或 ID 不是 UUID，无法可靠使用 codex delete。',
        }.get(agent)
        result['detail'] = '根据原始 session ID 定位此记录，仅删除下方这一个会话文件。'
    if args:
        result['native_command'] = shlex.join(args)
    result['command'] = result['native_command'] or result['file_command']
    result['method'] = 'native' if result['native_command'] else 'file' if result['file_command'] else None
    if not result['command']:
        result['reason'] = '来源路径含删除守卫禁止的字符，无法生成单文件删除命令'
    result['detail'] += ' 在终端手动执行删除命令后，Harnession 已索引的历史快照仍会保留。'
    return result


def resume_recipe(agent, native_id, source, root, cwd=None):
    result = dict(command=None, session_id=str(native_id or ''), target=source, detail='', reason=None)
    path, root, reason = _session_source(agent, native_id, source, root)
    if reason:
        result['reason'] = reason
        return result
    directory = None
    if cwd:
        directory = Path(cwd).expanduser()
        if not directory.is_absolute() or not directory.is_dir() or any(ord(c) < 32 for c in str(directory)):
            result['reason'] = '原会话工作目录不可用，无法生成准确的恢复命令'
            return result
    if agent == 'codex':
        home = _agent_home(path, 'sessions')
        if not home or not _is_uuid(native_id):
            result['reason'] = '恢复需要原生 sessions 目录及有效 UUID；导入文件不能保证被 Codex CLI 定位'
            return result
        args = ['env', 'CODEX_HOME=' + str(home), 'codex']
        if directory:
            args += ['--cd', str(directory)]
        args += ['resume', '--', native_id]
    elif agent == 'claude':
        home = _agent_home(path, 'projects')
        if not home or not _is_uuid(native_id):
            result['reason'] = '恢复需要原生 projects 目录及有效 UUID；导入文件不能保证被 Claude CLI 定位'
            return result
        args = ['env', 'CLAUDE_CONFIG_DIR=' + str(home), 'claude', '--resume', native_id]
    elif agent == 'pi':
        # An exact path also works for imported sessions and avoids partial-ID lookup.
        home = _agent_home(path, 'sessions')
        args = ['env', 'PI_CODING_AGENT_DIR=' + str(home)] if home else []
        args += ['pi', '--session-dir', str(root), '--session', str(path)]
    else:
        args = ['env', 'HERMES_HOME=' + str(path.parent), 'hermes', '--resume', native_id]
    command = shlex.join(args)
    if directory and agent != 'codex':
        command = shlex.join(['cd', '--', str(directory)]) + ' && ' + command
    result.update(command=command, target=str(path),
                  detail='使用原始 session ID 或精确来源文件继续已有会话；在终端手动执行，不会创建副本。')
    return result


def _agent_home(path, storage_name):
    return next((parent.parent for parent in path.parents if parent.name == storage_name), None)


def _session_source(agent, native_id, source, root):
    if agent not in ('codex', 'claude', 'pi', 'hermes'):
        return None, None, '此 Agent 尚未支持会话命令'
    if not isinstance(native_id, str) or not native_id.strip() or any(ord(c) < 32 for c in native_id):
        return None, None, '缺少有效的原始 session ID'
    if native_id.startswith('-'):
        return None, None, '原始 session ID 不能以选项前缀开头'
    if not source or not root:
        return None, None, '缺少会话来源路径'
    if agent == 'hermes':
        filename, separator, source_id = source.rpartition('#')
        if not separator or source_id != native_id:
            return None, None, '来源中的 session ID 与当前会话不一致'
    else:
        filename = source
    path = Path(filename)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        return None, None, '原始会话文件不存在或已变为符号链接'
    path = path.resolve()
    root = Path(root).expanduser().resolve()
    try:
        if agent == 'hermes':
            if path != root or path.name != 'state.db':
                raise ValueError
        else:
            path.relative_to(root)
    except ValueError:
        return None, None, '来源不在当前数据源内，或原生 CLI 无法定位此数据库'
    return path, root, None


def _is_uuid(value):
    try:
        UUID(value)
        return True
    except ValueError:
        return False
