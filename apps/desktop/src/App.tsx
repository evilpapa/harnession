import { lazy, Suspense, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type InfiniteData } from '@tanstack/react-query';
import * as Dialog from '@radix-ui/react-dialog';
import {
  SquaresFour, MagnifyingGlass, Database, TerminalWindow, ChatCircleText,
  Columns, Cube, Plus, ArrowClockwise, FolderSimple, X, WarningCircle, Copy, Check,
  ArrowUpRight, SidebarSimple, Play, Stop, SpinnerGap, ArrowLeft,
  Brain, ShieldCheck, CaretRight, Tag, Trash, PauseCircle, XCircle, List, Sun, Moon, PencilSimple,
} from '@phosphor-icons/react';
import { closeRun, index, listAgents, listRuns, native, startRun, stopRun } from './api';
import { useWorkspace } from './store';
import { AgentLogo } from './AgentLogo';
import { displayPath, projectLabel } from './pathDisplay';
import { groupMessages } from './messageGroups';
import { historyBatchSize, loadAllHistoryClick, readHistoryBatch } from './historyPagination';
import type { Artifact, Bootstrap, ColorTheme, Message, Page, Session, SessionDetail, Source, ViewMode } from './types';

const agentLabels: Record<string, string> = { codex: 'Codex', claude: 'Claude Code', pi: 'Pi', hermes: 'Hermes', antigravity: 'Antigravity', opencode: 'OpenCode', shell: '系统终端' };
const TerminalPane = lazy(() => import('./TerminalPane').then((module) => ({ default: module.TerminalPane })));
const Markdown = lazy(() => import('./Markdown'));
const statusLabels: Record<string, string> = { ok: '已扫描', partial: '部分失败', error: '扫描失败', missing: '未找到来源', unsupported: '尚未支持', unscanned: '尚未扫描' };
const turnStatuses = {
  interrupted: { label: '本轮已中断', icon: PauseCircle },
  cancelled: { label: '本轮已取消', icon: XCircle },
  failed: { label: '本轮执行失败', icon: WarningCircle },
};
const formatDate = (value: string | null) => {
  if (!value) return '时间未知';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(date);
};
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
type RenamedSession = Pick<Session, 'title' | 'indexed_at'>;

function Modal({ open, onOpenChange, title, description, children, wide = false }: {
  open: boolean; onOpenChange: (open: boolean) => void; title: string; description: string;
  children: ReactNode; wide?: boolean;
}) {
  return <Dialog.Root open={open} onOpenChange={onOpenChange}><Dialog.Portal>
    <Dialog.Overlay className="modal-overlay" />
    <Dialog.Content className={`modal ${wide ? 'wide' : ''}`}>
      <div className="modal-heading"><Dialog.Title>{title}</Dialog.Title><Dialog.Close className="icon-button" aria-label="关闭"><X /></Dialog.Close></div>
      <Dialog.Description className="modal-description">{description}</Dialog.Description>
      {children}
    </Dialog.Content>
  </Dialog.Portal></Dialog.Root>;
}

function CopyButton({ text, label = '复制', title }: { text: string; label?: string; title?: string }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState(false);
  return <button className="copy-button" aria-label={label} title={title || label} onClick={() => {
    setError(false);
    void navigator.clipboard.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1600); }).catch(() => setError(true));
  }}>{copied ? <Check /> : <Copy />}<span>{error ? '复制失败' : copied ? '已复制' : label}</span></button>;
}

function RenameSession({ session, onRenamed }: {
  session: SessionDetail; onRenamed: (id: string, result: RenamedSession) => void;
}) {
  const [draft, setDraft] = useState<{ title: string; original: string; revision: string } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const rename = useMutation({
    mutationFn: (values: NonNullable<typeof draft>) => index<RenamedSession>('rename_session', {
      session_id: session.id, indexed_at: values.revision, title: values.title,
    }),
    onSuccess: (result) => {
      setDraft(null);
      onRenamed(session.id, result);
    },
  });
  const reason = session.rename_unavailable_reason;
  const title = draft?.title.trim() ?? '';
  const tooLong = [...title].length > session.rename_title_limit;
  const canSave = Boolean(draft && title && title !== draft.original && !tooLong && !reason && !rename.isPending);
  return <Dialog.Root open={Boolean(draft)} onOpenChange={(open) => {
    if (rename.isPending) return;
    rename.reset();
    setDraft(open ? { title: session.title, original: session.title, revision: session.indexed_at } : null);
  }}>
    <Dialog.Trigger className="icon-button rename-title-button" aria-label="修改会话标题" title="修改会话标题">
      <PencilSimple aria-hidden="true" />
    </Dialog.Trigger>
    <Dialog.Portal>
      <Dialog.Overlay className="modal-overlay" />
      <Dialog.Content className="modal" onOpenAutoFocus={(event) => {
        if (!inputRef.current) return;
        event.preventDefault();
        inputRef.current.focus();
        inputRef.current.select();
      }}>
        <div className="modal-heading">
          <Dialog.Title>修改会话标题</Dialog.Title>
          <Dialog.Close className="icon-button" aria-label="关闭" disabled={rename.isPending}><X aria-hidden="true" /></Dialog.Close>
        </div>
        <Dialog.Description className="modal-description">
          {reason || `保存后，标题将同步到 Harnession 和 ${agentLabels[session.agent] || session.agent}。`}
        </Dialog.Description>
        {!reason && draft && <form onSubmit={(event) => {
          event.preventDefault();
          if (canSave) rename.mutate(draft);
        }}>
          <label className="field-label" htmlFor="rename-session-title">新标题</label>
          <input id="rename-session-title" ref={inputRef} value={draft.title} required readOnly={rename.isPending}
            aria-invalid={tooLong || undefined} aria-describedby="rename-title-hint"
            onChange={(event) => {
              setDraft({ ...draft, title: event.target.value });
              if (rename.isError) rename.reset();
            }} />
          <p id="rename-title-hint" className={tooLong ? 'form-error' : 'form-hint'}>
            最多 {session.rename_title_limit} 个字符
          </p>
          {rename.isError && <p className="form-error" role="alert">{errorText(rename.error)}</p>}
          <div className="modal-footer rename-footer">
            <Dialog.Close className="quiet-button" disabled={rename.isPending}>取消</Dialog.Close>
            <button type="submit" className="primary-button" disabled={!canSave}>
              {rename.isPending && <SpinnerGap className="spin" aria-hidden="true" />}
              {rename.isPending ? '同步中…' : '保存标题'}
            </button>
          </div>
        </form>}
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}

function DeletionCommand({ session, confirmDelete, refresh }: {
  session: SessionDetail; confirmDelete: (session: SessionDetail, trigger: HTMLButtonElement) => void;
  refresh: () => void;
}) {
  const recipe = session.deletion;
  const resume = session.resumption;
  const directButton = <button className="copy-button danger-button" disabled={!native || Boolean(recipe.direct_reason)}
    title={recipe.direct_reason || '删除原始会话和本地缓存'} onClick={(event) => confirmDelete(session, event.currentTarget)}>
    <Trash aria-hidden="true" />直接删除
  </button>;
  return <div className="session-deletion" role="group" aria-label="会话命令">
    {recipe.command ? <CopyButton text={recipe.command} label="复制删除命令" title={`${recipe.method === 'native' ? 'Agent 原生删除' : '单文件删除'} · ${recipe.session_id}\n${recipe.command}`} /> :
      <button className="copy-button" disabled title={recipe.reason || '无法生成删除命令'}>{session.source_missing ? '仅有本地索引' : '删除命令不可用'}</button>}
    {resume?.command ? <CopyButton text={resume.command} label="复制恢复命令" title={`继续原会话 · ${resume.session_id}\n${resume.command}`} /> :
      <button className="copy-button" disabled title={resume?.reason || '无法生成恢复命令'}>恢复命令不可用</button>}
    <details className="deletion-details" onToggle={(event) => { if (event.currentTarget.open) refresh(); }}><summary aria-label="查看会话命令详情" title="查看会话命令详情"><TerminalWindow /></summary>
      <div className="deletion-preview"><strong>{agentLabels[session.agent] || session.agent} · 会话命令</strong>
        <dl><dt>SESSION ID</dt><dd>{recipe.session_id || '未知'}</dd><dt>目标</dt><dd>{recipe.target}</dd></dl>
        {recipe.native_command && <section className="command-option"><h3>原生删除</h3><pre><code>{recipe.native_command}</code></pre><div className="command-actions"><CopyButton text={recipe.native_command} label="复制原生删除命令" />{!recipe.file_command && directButton}</div></section>}
        {recipe.file_command && <section className="command-option"><h3>单文件删除</h3><pre><code>{recipe.file_command}</code></pre><div className="command-actions"><CopyButton text={recipe.file_command} label="复制文件删除命令" />{directButton}</div></section>}
        {recipe.native_reason && <p className="command-note">{recipe.native_reason}</p>}
        <p>{recipe.reason || recipe.detail}</p>
        {recipe.command && <small>点击复制后，在终端手动执行。</small>}
        {session.source_missing && <section className="command-option">
          <button className="copy-button danger-button" disabled={!native}
            title={native ? '删除此会话在 Harnession 中的记录' : '删除索引仅在桌面应用中可用'}
            onClick={(event) => confirmDelete(session, event.currentTarget)}>
            <Trash aria-hidden="true" />删除索引
          </button>
        </section>}
        {recipe.command && recipe.direct_reason && <p className="command-note">{recipe.direct_reason}</p>}
        <section className="command-option"><h3>恢复原会话</h3>{resume?.command && <><pre><code>{resume.command}</code></pre><CopyButton text={resume.command} label="复制完整恢复命令" /></>}<p>{resume?.reason || resume?.detail || '无法生成恢复命令'}</p></section>
      </div>
    </details>
  </div>;
}

function DeleteSessionDialog({ target, busy, error, close, confirm, restoreFocus }: {
  target: SessionDetail | null; busy: boolean; error: unknown; close: () => void;
  confirm: (session: SessionDetail) => void; restoreFocus: () => void;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const indexOnly = target?.source_missing;
  return <Dialog.Root open={Boolean(target)} onOpenChange={(open) => { if (!open && !busy) close(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="modal-overlay" />
      <Dialog.Content className="modal" role="alertdialog" aria-busy={busy}
        onOpenAutoFocus={(event) => { event.preventDefault(); cancelRef.current?.focus(); }}
        onCloseAutoFocus={(event) => { event.preventDefault(); restoreFocus(); }}
        onPointerDownOutside={(event) => event.preventDefault()}>
        <div className="modal-heading"><Dialog.Title>{indexOnly ? '删除本地会话索引？' : '永久删除会话？'}</Dialog.Title></div>
        <Dialog.Description className="modal-description delete-description">
          {indexOnly ? '将删除该会话在 Harnession 中保存的消息、附件缓存和项目归属。' : '原始会话文件和 Harnession 缓存将一并删除。'}<strong>此操作无法恢复。</strong>
        </Dialog.Description>
        {target && <div className="delete-target"><strong>{target.title}</strong>
          <span>{agentLabels[target.agent] || target.agent} · {target.native_id}</span></div>}
        {error != null && <p className="delete-error" role="alert">{errorText(error)}</p>}
        <div className="modal-footer delete-footer">
          <Dialog.Close ref={cancelRef} className="quiet-button" disabled={busy}>取消</Dialog.Close>
          <button className="primary-button danger-button" disabled={busy || !target} onClick={() => target && confirm(target)}>
            {busy ? <SpinnerGap className="spin" aria-hidden="true" /> : <Trash aria-hidden="true" />}{busy ? '删除中…' : indexOnly ? '确认删除索引' : '确认删除'}
          </button>
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}

function MessageCard({ message, agent, query, openArtifact }: { message: Message; agent: string; query: string; openArtifact: (value: Artifact) => void }) {
  const [expanded, setExpanded] = useState(false);
  if (message.kind && message.kind !== 'message') return <TechnicalMessage message={message} query={query} openArtifact={openArtifact} />;
  const trimmed = message.text.length > 14000 && !expanded;
  const match = query && message.text.includes(query);
  return <article className={`message ${message.role} ${match ? 'match' : ''}`} id={`message-${message.id}`}>
    <div className={`persona ${message.role}`} aria-hidden="true">{message.role === 'user' ? 'YOU' : message.role === 'archive' ? 'LOG' : <AgentLogo agent={agent} size="avatar" />}</div>
    <div className="message-content"><div className="message-meta"><strong>{message.role === 'user' ? '你' : message.role === 'archive' ? '旧版文本记录' : agentLabels[agent] || agent}</strong><time>{formatDate(message.timestamp)}</time><span className="message-location" title={message.source_locator}>{message.source_locator}</span><CopyButton text={message.text} label="复制消息" /></div>
      {message.parent_event_id && <div className="branch-parent">父记录 {message.parent_event_id.slice(0, 8)} · 原始关系</div>}
      <div className="message-body"><Suspense fallback={<div className="markdown-loading">正在渲染内容…</div>}><Markdown text={trimmed ? message.text.slice(0, 14000) + '\n\n…' : message.text} /></Suspense>
        {trimmed && <button className="text-button" onClick={() => setExpanded(true)}>展开完整消息（{message.text.length.toLocaleString()} 字符）</button>}
        {message.attachments.map((artifact) => <button className="attachment" key={artifact.id} onClick={() => openArtifact(artifact)} disabled={!artifact.available}>
          <div className="attachment-glyph"><Cube /></div><span><strong>{artifact.name}</strong><small>{artifact.available ? artifact.mime || '本地图片 · 点击预览' : artifact.reason}</small></span><ArrowUpRight />
        </button>)}
      </div>
    </div>
  </article>;
}

function TechnicalMessage({ message, query, openArtifact, terminal = false }: {
  message: Message; query: string; openArtifact: (value: Artifact) => void; terminal?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const status = message.kind === 'turn_status' ? message.metadata?.turn_status : undefined;
  const turnStatus = status && turnStatuses[status];
  const labels = { tool_call: '工具调用', tool_output: '工具输出', thinking: '思考记录', system: message.role === 'developer' ? '开发者提示' : '系统提示', instruction: '会话指令', message: '记录', turn_status: '会话状态' };
  const icons: Partial<Record<Message['kind'], typeof TerminalWindow>> = {
    thinking: Brain, system: ShieldCheck, instruction: Tag, turn_status: turnStatus ? turnStatus.icon : PauseCircle,
  };
  const Icon = icons[message.kind] || TerminalWindow;
  const match = Boolean(query && message.text.includes(query));
  const trimmed = message.text.length > 14000 && !expanded;
  return <article className={`technical-message ${message.kind} ${status || ''} ${terminal ? 'terminal-event' : ''} ${match ? 'match' : ''}`}
    id={terminal ? undefined : `message-${message.id}`}>
    <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary><CaretRight className="disclosure-chevron" aria-hidden="true" /><Icon aria-hidden="true" /><strong>{turnStatus ? turnStatus.label : labels[message.kind] || '技术记录'}</strong>
        {message.kind === 'instruction' && <span className={`instruction-tag ${message.metadata?.instruction_category || ''}`}>{message.metadata?.instruction_label || '上下文'}</span>}
        {message.metadata?.tool_name && <span className="technical-tool" title={message.metadata.tool_name}>{message.metadata.tool_name}</span>}
        {message.metadata?.is_error && <span className="technical-error">执行失败</span>}
        {message.metadata?.unavailable && <span className="technical-unavailable">原文不可读</span>}
        {match && <span className="technical-match">命中搜索</span>}
        {message.kind !== 'turn_status' &&
          <span className="technical-size">{message.text.length.toLocaleString()} 字符</span>}
        <time>{formatDate(message.timestamp)}</time>
      </summary>
      {open && <div className="technical-body">
        <div className="technical-meta"><span title={message.source_locator}>{message.source_locator}</span>
          {message.metadata?.tool_call_id && <span title={message.metadata.tool_call_id}>CALL {message.metadata.tool_call_id}</span>}
          <CopyButton text={message.text} label="复制完整记录" />
        </div>
        <pre className="technical-text">{message.text ? (trimmed ? message.text.slice(0, 14000) + '\n\n…' : message.text) : '来源未提供文本内容。'}</pre>
        {trimmed && <button className="text-button" onClick={() => setExpanded(true)}>展开完整记录（{message.text.length.toLocaleString()} 字符）</button>}
        {message.attachments.map((artifact) => <button className="attachment" key={artifact.id} onClick={() => openArtifact(artifact)} disabled={!artifact.available}>
          <div className="attachment-glyph"><Cube /></div><span><strong>{artifact.name}</strong><small>{artifact.available ? artifact.mime || '本地图片 · 点击预览' : artifact.reason}</small></span><ArrowUpRight />
        </button>)}
      </div>}
    </details>
  </article>;
}

function TechnicalGroup({ messages, query, openArtifact, terminal = false }: {
  messages: Message[]; query: string; openArtifact: (value: Artifact) => void; terminal?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const counts = [
    ['thinking', '思考'], ['tool_call', '工具调用'], ['tool_output', '工具输出'], ['system', '系统提示'],
  ].map(([kind, label]) => ({ kind, label, count: messages.filter((message) => message.kind === kind).length }));
  const matches = query ? messages.filter((message) => message.text.includes(query)).length : 0;
  const errors = messages.filter((message) => message.metadata?.is_error).length;
  return <details className={`technical-group ${terminal ? 'terminal-group' : ''} ${matches ? 'match' : ''}`}
    open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>
      <CaretRight className="disclosure-chevron" aria-hidden="true" /><SquaresFour aria-hidden="true" />
      <strong>过程记录</strong><span className="group-total">{messages.length} 条</span>
      <span className="group-breakdown">{counts.filter((item) => item.count).map((item) =>
        <span key={item.kind}>{item.label} {item.count}</span>)}</span>
      {errors > 0 && <span className="technical-error">{errors} 条执行失败</span>}
      {matches > 0 && <span className="technical-match">{matches} 条命中搜索</span>}
      <span className="group-action">{open ? '收起' : '展开'}</span>
    </summary>
    {open && <div className="technical-group-body">{messages.map((message) =>
      <TechnicalMessage key={message.id} message={message} query={query} openArtifact={openArtifact} terminal={terminal} />)}
      <button className="group-collapse" onClick={(event) => {
        const details = event.currentTarget.closest('details')!;
        // Keep keyboard focus and the viewport at the group when hiding a long run.
        const summary = details.querySelector('summary');
        summary?.focus({ preventScroll: true });
        summary?.scrollIntoView({ block: 'nearest' });
        setOpen(false);
      }}>收起 {messages.length} 条过程记录</button>
    </div>}
  </details>;
}

function Sources({ sources, busy, scan }: { sources: Source[]; busy: boolean; scan: (paths: Record<string, string>) => void }) {
  const [paths, setPaths] = useState<Record<string, string>>({});
  useEffect(() => setPaths(Object.fromEntries(sources.map((source) => [source.agent, source.path]))), [sources]);
  return <><div className="source-list">{sources.map((source) => <section className="source-card" key={source.agent}>
    <div className="source-title"><span><AgentLogo agent={source.agent} />{agentLabels[source.agent] || source.agent}</span><span className={`status-tag ${source.status}`}>{statusLabels[source.status] || source.status}</span></div>
    <label htmlFor={`path-${source.agent}`}>数据位置</label><input id={`path-${source.agent}`} value={paths[source.agent] ?? source.path} onChange={(event) => setPaths({ ...paths, [source.agent]: event.target.value })} disabled={source.status === 'unsupported' || busy} />
    <p>{source.detail}</p><small>扫描时间：{formatDate(source.scanned_at)}</small>
  </section>)}</div><div className="modal-footer"><span>扫描在本地进行，不修改原始来源。</span><button className="primary-button" onClick={() => scan(paths)} disabled={busy}>{busy ? <SpinnerGap className="spin" /> : <ArrowClockwise />}{busy ? '扫描中…' : '保存路径并扫描'}</button></div></>;
}

export function App() {
  const queryClient = useQueryClient();
  const workspace = useWorkspace();
  const { mode, selected, project, agent, inspector, focus, activeRun, terminalKind, patch, selectProject, selectAgent } = workspace;
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [dialog, setDialog] = useState<'sources' | 'settings' | 'start' | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<SessionDetail | null>(null);
  const deleteTrigger = useRef<HTMLButtonElement | null>(null);
  const [notice, setNotice] = useState<string | { text: string; duration: number }>('');
  const [picture, setPicture] = useState<Artifact | null>(null);
  const [startAgent, setStartAgent] = useState('codex');
  const [directory, setDirectory] = useState('');
  const [fromStart, setFromStart] = useState(false);
  const [theme, setTheme] = useState<ColorTheme>(() => localStorage.getItem('harnession.theme') === 'light' ? 'light' : 'dark');
  const themeAction = theme === 'light' ? '切换到夜间模式' : '切换到日间模式';
  const [sessionLayout, setSessionLayout] = useState<'list' | 'cards'>(() =>
    localStorage.getItem('harnession.session-layout') === 'cards' ? 'cards' : 'list');
  const [detailOpen, setDetailOpen] = useState(false);
  const cardView = sessionLayout === 'cards';
  const browseCards = cardView && !detailOpen;
  const [projectDrawer, setProjectDrawer] = useState(false);
  const [splitPercent, setSplitPercent] = useState(50);
  const searchRef = useRef<HTMLInputElement>(null);
  const backRef = useRef<HTMLButtonElement>(null);
  const sessionCardTrigger = useRef<HTMLButtonElement | null>(null);
  const chatRef = useRef<HTMLDivElement>(null);
  const historyScroll = useRef<{ pane: HTMLElement; pageCount: number } | null>(null);
  useLayoutEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = theme;
    localStorage.setItem('harnession.theme', theme);
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', getComputedStyle(root).getPropertyValue('--canvas').trim());
  }, [theme]);
  useEffect(() => {
    if (typeof notice === 'string') return;
    const timer = setTimeout(() => setNotice((current) => current === notice ? '' : current), notice.duration);
    return () => clearTimeout(timer);
  }, [notice]);
  useEffect(() => { const timer = setTimeout(() => setQuery(search), 220); return () => clearTimeout(timer); }, [search]);
  useEffect(() => { localStorage.setItem('harnession.session-layout', sessionLayout); }, [sessionLayout]);
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.target instanceof Element && event.target.closest('.pty-host, [role="dialog"]')) return;
      if (deleteTarget) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        patch({ focus: false });
        setDetailOpen(false);
        setProjectDrawer(false);
        requestAnimationFrame(() => searchRef.current?.focus());
      }
    };
    window.addEventListener('keydown', handler); return () => window.removeEventListener('keydown', handler);
  }, [patch, deleteTarget]);

  const bootstrap = useQuery({ queryKey: ['bootstrap'], queryFn: () => index<Bootstrap>('bootstrap') });
  const sessions = useInfiniteQuery({
    queryKey: ['sessions', project, agent, query], initialPageParam: { cursor: 0, generation: null as string | null },
    queryFn: async ({ pageParam }) => {
      const page = await index<Page<Session>>('list_sessions', { project, agent, query, ...pageParam, limit: 50 });
      if (page.reset) throw new Error('索引已更新，请重新读取列表。');
      return page;
    },
    getNextPageParam: (page) => page.next_cursor === null ? undefined : { cursor: page.next_cursor, generation: page.generation },
  });
  const records = sessions.data?.pages.flatMap((page) => page.items) ?? [];
  const data = bootstrap.data;
  const homePath = (value: string | null | undefined) => displayPath(value, data?.home_directory);
  const leaf = (value: string | null | undefined) => projectLabel(value, data?.home_directory);
  useEffect(() => {
    if (cardView) return;
    if (sessions.isSuccess && records.length && !records.some((record) => record.id === selected)) patch({ selected: records[0].id });
    if (sessions.isSuccess && !records.length) patch({ selected: null });
  }, [sessions.data, sessions.isSuccess, selected, cardView]);
  const detailEnabled = Boolean(selected) && !browseCards;
  const detail = useQuery({ queryKey: ['session', selected], queryFn: () => index<SessionDetail>('get_session', { session_id: selected }), enabled: detailEnabled });
  const session = detail.data;
  const messages = useInfiniteQuery({
    queryKey: ['events', selected, query, fromStart],
    enabled: detailEnabled,
    initialPageParam: { cursor: 0, generation: null as string | null, click: 0, restartFromBeginning: false },
    queryFn: async ({ pageParam, signal }) => {
      const page = await readHistoryBatch<Message>(
        pageParam,
        historyBatchSize(pageParam.click),
        (params) => index<Page<Message>>('read_events', {
          session_id: selected,
          query: fromStart || pageParam.restartFromBeginning ? '' : query,
          ...params,
        }),
        signal,
      );
      return { ...page, restartedFromBeginning: pageParam.restartFromBeginning };
    },
    getNextPageParam: (page, pages) => {
      if (page.next_cursor === null) return undefined;
      const click = pages.length;
      const restartFromBeginning = click === loadAllHistoryClick && Boolean(pages[0].located);
      return {
        cursor: restartFromBeginning ? 0 : page.next_cursor,
        generation: page.generation,
        click,
        restartFromBeginning,
      };
    },
  });
  const readFromBeginning = messages.data?.pages.at(-1)?.restartedFromBeginning;
  const events = useMemo(() => {
    const pages = messages.data?.pages ?? [];
    return (readFromBeginning ? pages.slice(-1) : pages).flatMap((page) => page.items);
  }, [messages.data, readFromBeginning]);
  const messageBlocks = useMemo(() => groupMessages(events), [events]);
  const nextHistoryClick = messages.data?.pages.length ?? 1;
  const loadAllNext = nextHistoryClick >= loadAllHistoryClick;
  useLayoutEffect(() => () => { historyScroll.current = null; }, [selected, query, fromStart]);
  useLayoutEffect(() => {
    const pending = historyScroll.current;
    if (!pending || nextHistoryClick <= pending.pageCount) return;
    historyScroll.current = null;
    pending.pane.scrollTop = pending.pane.scrollHeight;
  }, [nextHistoryClick]);
  const historyLoadButton = messages.hasNextPage && (
    <button className="load-more" disabled={messages.isFetching}
      onClick={(event) => {
        const pane = event.currentTarget.closest<HTMLElement>('.chat-pane, .terminal-record');
        if (!pane) return;
        const pending = { pane, pageCount: nextHistoryClick };
        historyScroll.current = pending;
        void messages.fetchNextPage({ cancelRefetch: false }).then((result) => {
          if (historyScroll.current === pending && (result.data?.pages.length ?? 0) <= pending.pageCount) {
            historyScroll.current = null;
          }
        });
      }}>
      {messages.isFetchingNextPage ? '读取中…' : loadAllNext ? '读取全部历史消息' : '继续读取历史消息'}
    </button>
  );
  useEffect(() => { setFromStart(false); if (chatRef.current) chatRef.current.scrollTop = 0; }, [selected, query]);
  const runsQuery = useQuery({ queryKey: ['runs'], queryFn: listRuns, refetchInterval: native ? 3000 : false });
  const agentsQuery = useQuery({ queryKey: ['agents'], queryFn: listAgents });
  const runs = runsQuery.data ?? [];
  const currentRun = runs.find((run) => run.id === activeRun);
  useEffect(() => {
    const run = runs.find((item) => item.session_id === selected);
    patch({ activeRun: run?.id ?? null, terminalKind: run ? 'live' : 'record' });
  }, [selected]);
  const scanMutation = useMutation({
    mutationFn: (paths: Record<string, string> | undefined) => index<Bootstrap>('scan_sources', paths ? { paths } : {}),
    onSuccess: async (result) => {
      queryClient.setQueryData(['bootstrap'], result);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['sessions'] }),
        queryClient.invalidateQueries({ queryKey: ['session'] }),
        queryClient.invalidateQueries({ queryKey: ['events'] }),
      ]);
      setNotice('扫描完成。来源的缺失、部分失败和覆盖情况可在数据源面板查看。');
    }, onError: (error) => setNotice(errorText(error)),
  });
  const startMutation = useMutation({
    mutationFn: () => startRun(startAgent, selected, directory),
    onSuccess: async (run) => {
      queryClient.setQueryData(['runs'], (previous: typeof runs | undefined) => [...(previous ?? []), run]);
      patch({ activeRun: run.id, terminalKind: 'live', mode: mode === 'chat' ? 'tui' : mode });
      setDialog(null); setDetailOpen(true);
    }, onError: (error) => setNotice(errorText(error)),
  });
  async function sessionRenamed(id: string, result: RenamedSession) {
    const listKey = { queryKey: ['sessions'] };
    const detailKey = { queryKey: ['session', id] };
    await Promise.all([queryClient.cancelQueries(listKey), queryClient.cancelQueries(detailKey)]);
    queryClient.setQueryData<SessionDetail>(detailKey.queryKey, (cached) => cached && ({ ...cached, ...result }));
    queryClient.setQueriesData<InfiniteData<Page<Session>>>(listKey, (cached) => cached && ({
      ...cached, pages: cached.pages.map((page) => ({
        ...page, items: page.items.map((item) => item.id === id ? { ...item, ...result } : item),
      })),
    }));
    setNotice({ text: '会话标题已同步。', duration: 3000 });
    await Promise.all([
      queryClient.invalidateQueries(listKey),
      queryClient.invalidateQueries({ queryKey: ['events', id] }),
    ]);
  }
  const deleteMutation = useMutation({
    mutationFn: (target: SessionDetail) => index<{ deleted: true }>(target.source_missing ? 'delete_session_index' : 'delete_session', {
      session_id: target.id, indexed_at: target.indexed_at,
    }),
    onSuccess: async (_result, target) => {
      const listKey = { queryKey: ['sessions'] };
      const sessionQueries = [
        { queryKey: ['session', target.id] }, { queryKey: ['events', target.id] },
        ...target.artifacts.map((artifact) => ({ queryKey: ['artifact', artifact.id] })),
      ];
      await Promise.all([listKey, ...sessionQueries].map((key) => queryClient.cancelQueries(key)));
      queryClient.setQueriesData<InfiniteData<Page<Session>>>(listKey, (cached) => cached && ({
        ...cached, pages: cached.pages.map((page) => ({ ...page, items: page.items.filter((item) => item.id !== target.id) })),
      }));
      if (useWorkspace.getState().selected === target.id) {
        patch({ selected: null, activeRun: null, terminalKind: 'record' });
        setDetailOpen(false);
        if (cardView) patch({ focus: false });
      }
      const artifactIds = new Set(target.artifacts.map((artifact) => artifact.id));
      setPicture((current) => current && artifactIds.has(current.id) ? null : current);
      sessionQueries.forEach((key) => queryClient.removeQueries(key));
      setDeleteTarget(null);
      setNotice({
        text: target.source_missing ? '本地会话索引已删除。' : '会话及本地缓存已删除。',
        duration: 3000,
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['bootstrap'] }),
        queryClient.invalidateQueries(listKey),
      ]);
    },
  });
  function confirmDelete(target: SessionDetail, trigger: HTMLButtonElement) {
    if (deleteMutation.isPending) return;
    deleteMutation.reset();
    deleteTrigger.current = trigger;
    setDeleteTarget(target);
  }
  const artifactQuery = useQuery({
    queryKey: ['artifact', picture?.id], enabled: Boolean(picture?.available),
    queryFn: () => index<{ data_url: string; name: string }>('read_artifact', { artifact_id: picture!.id }),
  });
  const sourceIssues = data?.sources.filter((source) => ['partial', 'error'].includes(source.status)).length ?? 0;
  const installedAgents = agentsQuery.data?.filter((item) => item.installed) ?? [];
  const path = session?.project ?? (project || null);
  function chooseSession(record: Session, trigger: HTMLButtonElement) {
    patch({ selected: record.id });
    setDetailOpen(true);
    if (cardView) {
      sessionCardTrigger.current = trigger;
      requestAnimationFrame(() => backRef.current?.focus());
    }
  }
  function returnToSessions() {
    setDetailOpen(false);
    if (cardView) {
      patch({ focus: false });
      requestAnimationFrame(() => {
        if (sessionCardTrigger.current?.isConnected) sessionCardTrigger.current.focus();
        else searchRef.current?.focus();
      });
    }
  }
  function changeSessionLayout(layout: typeof sessionLayout) {
    if (layout === sessionLayout) return;
    setSessionLayout(layout);
    setDetailOpen(false);
    patch({ focus: false });
  }
  function closeProjectDrawer() {
    setProjectDrawer(false);
    if (cardView) setDetailOpen(false);
  }
  function openStart() {
    setDirectory(session?.cwd || project || '');
    const preferred = installedAgents.find((item) => item.id === session?.agent) || installedAgents[0];
    setStartAgent(preferred?.id ?? 'shell'); setDialog('start');
  }
  const views: [ViewMode, ReactNode, string][] = [['chat', <ChatCircleText />, 'Chat'], ['tui', <TerminalWindow />, 'TUI'], ['split', <Columns />, '并排']];
  const terminalRecord = useMemo(() => {
    let remaining = 500000;
    return new Map(events.map((event): [string, string] => {
      if (event.kind && event.kind !== 'message') return [event.id, ''];
      const text = event.text.slice(0, Math.min(remaining, 100000));
      remaining -= text.length;
      return [event.id, `${event.role.toUpperCase()} › ${text}${text.length < event.text.length ? '\n[记录已折叠，可在 Chat 中复制完整内容]' : ''}`];
    }));
  }, [events]);

  return <div className={`application ${cardView ? 'card-layout' : ''} ${focus ? 'focus-layout' : ''} ${detailOpen ? 'show-detail' : ''}`}>
    <div className="workspace-layout">
      {projectDrawer && <button className="sidebar-scrim" aria-label="关闭项目筛选" onClick={() => setProjectDrawer(false)} />}
      <aside id="workspace-sidebar" className={`project-sidebar ${projectDrawer ? 'drawer-open' : ''}`} aria-label="项目与智能体">
        <div className="sidebar-header">
          <button className="brand" aria-label="关于 Harnession" title="关于 Harnession" onClick={() => setDialog('settings')}>
            <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span><span>HARNESSION</span>
          </button>
          <button type="button" className="icon-button theme-switch" aria-label={themeAction} title={themeAction}
            onClick={() => setTheme((current) => current === 'light' ? 'dark' : 'light')}>
            {theme === 'light' ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
          </button>
        </div>
        <div className="sidebar-filters">
          <div className="section-label">检测到的智能体</div>
          <div className="agent-list" role="group" aria-label="Agent 筛选">
            <button className={`agent-row ${!agent && project === null ? 'active' : ''}`} aria-pressed={!agent && project === null} onClick={() => { selectAgent(''); closeProjectDrawer(); }}><AgentLogo agent="all" /><span>全部来源</span><span className="count-badge">{data?.total.toLocaleString() ?? '—'}</span></button>
            {Object.entries(agentLabels).filter(([id]) => id !== 'shell').map(([id, label]) => <button className={`agent-row ${agent === id ? 'active' : ''}`} key={id} aria-pressed={agent === id} onClick={() => { selectAgent(id); closeProjectDrawer(); }}><AgentLogo agent={id} /><span>{label}</span><span className="count-badge">{(data?.counts[id] ?? 0).toLocaleString()}</span></button>)}
          </div>
          <div className="section-label project-section">项目</div>
          <div className="project-list" role="group" aria-label="项目筛选">
            {data?.projects.map((item) => <button key={item.id ?? 'unclassified'} className={`project-row ${project === (item.id ?? '') ? 'active' : ''}`} aria-pressed={project === (item.id ?? '')} title={homePath(item.path) || '未归类'} onClick={() => { selectProject(item.id ?? ''); closeProjectDrawer(); }}>
              <FolderSimple /><span className="project-name">{leaf(item.path)}</span>
              <span className="count-badge" title="项目会话总数">{item.count.toLocaleString()}</span>
            </button>)}
          </div>
        </div>
        <div className="sidebar-footer">
          <button className={`source-summary ${sourceIssues ? 'warning' : ''}`} onClick={() => setDialog('sources')} title="数据源配置">
            <Database aria-hidden="true" /><span>数据源配置</span>
            {sourceIssues > 0 && <span className="count-badge" title={`${sourceIssues} 个来源需要关注`}>{sourceIssues}</span>}
            <ArrowUpRight aria-hidden="true" />
          </button>
        </div>
      </aside>
      <section className={`session-sidebar ${cardView && detailOpen ? 'hidden' : ''}`} aria-label="会话列表">
        <div className="session-list-head"><div className="session-list-title"><h2 onDoubleClick={cardView ? undefined : () => patch({ focus: !focus })}>会话</h2><span className="count-badge" title="会话总数">{data?.total.toLocaleString() ?? '—'}</span><button className="icon-button project-drawer-button" aria-label="项目与 Agent 筛选" aria-controls="workspace-sidebar" aria-expanded={projectDrawer} onClick={() => setProjectDrawer(!projectDrawer)}><SidebarSimple /></button><button className="icon-button" aria-label="扫描本地会话" title="扫描本地会话" disabled={scanMutation.isPending} onClick={() => scanMutation.mutate(undefined)}><ArrowClockwise className={scanMutation.isPending ? 'spin' : ''} /></button></div>
          <div className="search-box"><MagnifyingGlass /><input ref={searchRef} aria-label="搜索标题与会话内容" placeholder="搜索标题、内容…" value={search} onChange={(event) => setSearch(event.target.value)} />{search ? <button className="icon-button" aria-label="清除搜索" onClick={() => setSearch('')}><X /></button> : <kbd>⌘ K</kbd>}</div>
        </div>
        <div className="list-context">
          <span>{query ? 'SEARCH RESULTS' : agent ? agentLabels[agent].toUpperCase() : 'RECENT CONTEXT'} <span className="count-badge">{records.length}{sessions.hasNextPage ? '+' : ''}</span></span>
          <div className="segmented-control" role="group" aria-label="会话展示方式">
            <button type="button" aria-pressed={!cardView} onClick={() => changeSessionLayout('list')}><List aria-hidden="true" />列表</button>
            <button type="button" aria-pressed={cardView} onClick={() => changeSessionLayout('cards')}><SquaresFour aria-hidden="true" />卡片</button>
          </div>
        </div>
        <div className="session-list">
          {sessions.isPending && <Loading label="读取本地会话…" />}
          {sessions.isError && <ErrorState error={sessions.error} retry={() => void sessions.refetch()} />}
          {records.map((record) => <button className={`session-row ${!cardView && selected === record.id ? 'active' : ''}`} key={record.id} aria-pressed={cardView ? undefined : selected === record.id} onClick={(event) => chooseSession(record, event.currentTarget)}>
            <span className="session-row-meta"><span><AgentLogo agent={record.agent} size="sm" />{agentLabels[record.agent] || record.agent}</span><time>{formatDate(record.updated || record.created)}</time></span>
            <h3>{record.title}</h3><p>{record.excerpt || '暂无用户提问'}</p><span className="session-row-footer">{leaf(record.project)}<span>{record.structured ? '历史记录' : '旧版文本'}{cardView && <ArrowUpRight aria-hidden="true" />}</span></span>
          </button>)}
          {!sessions.isPending && !sessions.isError && records.length === 0 && <div className="empty-state small"><MagnifyingGlass /><h3>{data?.total ? '没有匹配的会话' : '从你的第一段上下文开始'}</h3><p>{data?.total ? '调整项目、Agent 或搜索词。' : '扫描本机 Agent 数据，按项目发现历史会话。'}</p><button className="text-button" onClick={() => data?.total ? (setSearch(''), patch({ project: null, agent: '' })) : scanMutation.mutate(undefined)} disabled={scanMutation.isPending}>{data?.total ? '清除筛选' : '扫描本地会话'}</button></div>}
          {sessions.hasNextPage && <button className="load-more" disabled={sessions.isFetchingNextPage} onClick={() => void sessions.fetchNextPage()}>{sessions.isFetchingNextPage ? '读取中…' : '加载更多会话'}</button>}
        </div>
      </section>
      <main className={`main-workspace ${browseCards ? 'hidden' : ''}`}>
        <header className="content-header"><div className="breadcrumb"><button ref={backRef} className={`icon-button ${cardView ? 'cards-back' : 'mobile-back'}`} aria-label={cardView ? '返回会话卡片' : '返回会话列表'} onClick={returnToSessions}><ArrowLeft aria-hidden="true" />{cardView && '返回卡片'}</button><span>WORKSPACE</span><span>/</span><b title={homePath(path) || '全部项目'}>{leaf(path)}</b>{session && <><span className="session-short-id">/ {session.id.slice(0, 8).toUpperCase()}</span><DeletionCommand key={session.id} session={session} confirmDelete={confirmDelete} refresh={() => void detail.refetch()} /></>}<button className="icon-button focus-button" aria-label={focus ? '还原工作区布局' : '聚焦内容'} title={focus ? '还原布局' : '聚焦内容'} onClick={() => patch({ focus: !focus })}><SidebarSimple /></button></div>
          <div className="content-title">
            <div className="session-title-heading">
              <h1>{session?.title || '上下文，汇聚于此。'}</h1>
              {session && <RenameSession key={session.id} session={session} onRenamed={sessionRenamed} />}
            </div>
            {session && <span className="status-tag ok">已索引</span>}
          </div>
          <p className="content-subtitle">{session ? <><span className="agent-identity"><AgentLogo agent={session.agent} />{agentLabels[session.agent] || session.agent}</span><i />{leaf(session.project)}<i />{formatDate(session.updated || session.created)}<i />本地历史会话</> : '跨 Agent 查找工作记录，读懂对话，回到创作现场。'}</p>
        </header>
        <div className="viewbar"><div className="view-tabs" role="tablist" aria-label="内容展示方式">{views.map(([value, icon, label], i) => <button key={value} id={`tab-${value}`} role="tab" aria-selected={mode === value} aria-controls="content-views" tabIndex={mode === value ? 0 : -1} className={mode === value ? 'active' : ''} onClick={() => patch({ mode: value })} onKeyDown={(event) => {
          let next = i;
          if (event.key === 'ArrowRight') next = (i + 1) % 3;
          else if (event.key === 'ArrowLeft') next = (i + 2) % 3;
          else if (event.key === 'Home') next = 0;
          else if (event.key === 'End') next = 2;
          else return;
          event.preventDefault(); patch({ mode: views[next][0] }); document.getElementById(`tab-${views[next][0]}`)?.focus();
        }}>{icon}{label}</button>)}</div><div className="viewbar-actions"><button className="quiet-button artifact-toggle" aria-expanded={inspector} aria-controls="inspector" onClick={() => patch({ inspector: !inspector })}><Cube /><span>产物</span><em>{session?.artifacts.length ?? 0}</em></button><button className="primary-button compact" onClick={native ? openStart : () => setNotice('网页预览支持真实索引和内容阅读。请使用 Tauri 桌面版启动 Agent 和交互终端。')} title={native ? '在此目录启动新的 Agent 运行' : '交互终端需使用桌面版'}><Plus /><span>{native ? '启动 Agent' : '桌面终端'}</span></button></div></div>
        <div id="content-views" className={`content-views mode-${mode}`} role="tabpanel" aria-labelledby={`tab-${mode}`}>
          <div className="reading-workspace" style={{ '--split': `${splitPercent}%` } as React.CSSProperties}>
            <div className={`chat-pane ${mode === 'tui' ? 'hidden' : ''}`} ref={chatRef}>
              {(detail.isPending && selected) || (messages.isPending && selected) ? <Loading label="读取会话内容…" /> : null}
              {detail.isError && <ErrorState error={detail.error} retry={() => void detail.refetch()} />}
              {messages.isError && <ErrorState error={messages.error} retry={() => void messages.refetch()} />}
              {session && <><div className="reading-label"><span>CONVERSATION</span><span>{session.structured ? 'SESSION RECORDS' : 'LEGACY ARCHIVE'}</span></div>
                {(!session.source_available || ['error', 'partial', 'missing'].includes(session.source_status)) && <div className="inline-notice warning"><WarningCircle />当前展示已索引的快照。{session.source_missing ? '原始会话已找不到。' : !session.source_available ? '原始来源暂不可用。' : '来源最近扫描存在异常，请查看数据源。'}</div>}
                {session.branch_status && <div className="inline-notice"><Columns />{session.branch_status}</div>}
                {!session.structured && <div className="inline-notice">旧索引没有可靠的消息边界。重新扫描可升级可访问的来源；这里保留完整原文。</div>}
                {messages.data?.pages[0].located && !fromStart && !readFromBeginning && <div className="search-location"><MagnifyingGlass />已定位包含「{query}」的消息<button onClick={() => setFromStart(true)}>从头阅读</button></div>}
                {messageBlocks.map((block) => block.type === 'technical_group'
                  ? <TechnicalGroup key={`${session.id}:group:${block.id}`} messages={block.messages} query={query} openArtifact={setPicture} />
                  : <MessageCard key={block.message.id} message={block.message} agent={session.agent} query={query} openArtifact={setPicture} />)}
                {events.length === 0 && !messages.isPending && !messages.isError && <div className="empty-state small"><ChatCircleText /><h3>暂无可读取的消息</h3><p>来源可能仅包含会话元信息。</p></div>}
                {historyLoadButton}
                <div className="coverage-note">{session.coverage}</div>
              </>}
              {!selected && !sessions.isPending && <div className="welcome"><div className="signal-orbit" aria-hidden="true"><span /><span /><span /><i /></div><span className="eyebrow">YOUR CONTEXT, CONNECTED.</span><h2>每一段对话，<br />都是下一步的起点。</h2><p>在左侧选择会话，或扫描本机数据。<br />项目、Chat、TUI 与产物，在同一处连接。</p><button className="primary-button" disabled={scanMutation.isPending} onClick={() => scanMutation.mutate(undefined)}><ArrowClockwise className={scanMutation.isPending ? 'spin' : ''} />{scanMutation.isPending ? '扫描中…' : '发现本地会话'}</button></div>}
            </div>
            {mode === 'split' && <div className="split-divider"><label>分栏<input aria-label="Chat 分栏宽度" type="range" min="30" max="70" step="5" value={splitPercent} onChange={(event) => setSplitPercent(Number(event.target.value))} /></label></div>}
            <section className={`terminal-pane ${mode === 'chat' ? 'hidden' : ''}`} aria-label="TUI 工作区"><div className="terminal-toolbar"><div className="terminal-tabs"><button className={terminalKind === 'record' ? 'active' : ''} onClick={() => patch({ terminalKind: 'record' })}>记录视图</button><button className={terminalKind === 'live' ? 'active' : ''} onClick={() => patch({ terminalKind: 'live' })}>交互终端 <span>{runs.length || ''}</span></button></div><span className="terminal-caption">{terminalKind === 'record' ? 'READ ONLY' : 'PTY'}</span></div>
              <div className={`terminal-record ${terminalKind !== 'record' ? 'hidden' : ''}`}><div className="terminal-record-brand"><span>HARNESSION</span> / {session ? <span className="agent-identity"><AgentLogo agent={session.agent} size="sm" />{agentLabels[session.agent] || session.agent}</span> : 'HISTORY'}</div><p className="terminal-record-label">◆ 终端风格记录 · 非原始终端回放</p>{messageBlocks.length ? messageBlocks.map((block) => block.type === 'technical_group'
                ? <TechnicalGroup key={`${session?.id}:group:${block.id}`} messages={block.messages} query={query} openArtifact={setPicture} terminal />
                : block.message.kind && block.message.kind !== 'message'
                  ? <TechnicalMessage key={block.message.id} message={block.message} query={query} openArtifact={setPicture} terminal />
                  : <pre className="terminal-message" key={block.message.id}>{terminalRecord.get(block.message.id)}</pre>) : <pre>选择一段会话以阅读记录。</pre>}{historyLoadButton}</div>
              <div className={`live-terminal ${terminalKind !== 'live' ? 'hidden' : ''}`}>
                {runs.length > 0 && <div className="run-controls"><label>运行<select aria-label="选择运行终端" value={activeRun ?? ''} onChange={(event) => patch({ activeRun: event.target.value })}><option value="" disabled>选择运行</option>{runs.map((run) => <option key={run.id} value={run.id}>{agentLabels[run.agent]} · {leaf(run.cwd)} · {run.state === 'running' ? '运行中' : `exit ${run.exit_code ?? '?'}`}</option>)}</select></label>{currentRun && <button className={`quiet-button ${currentRun.state === 'running' ? 'danger' : ''}`} onClick={() => {
                  const action = currentRun.state === 'running' ? stopRun : closeRun;
                  void action(currentRun.id).then(() => {
                    if (currentRun.state === 'exited') patch({ activeRun: runs.find((run) => run.id !== currentRun.id)?.id ?? null });
                    return queryClient.invalidateQueries({ queryKey: ['runs'] });
                  }).catch((error) => setNotice(errorText(error)));
                }}>{currentRun.state === 'running' ? <Stop /> : <X />}{currentRun.state === 'running' ? '结束进程' : '关闭终端'}</button>}</div>}
                <div className="terminal-deck"><Suspense fallback={<Loading label="加载终端组件…" />}>{runs.map((run) => <TerminalPane key={run.id} run={run} theme={theme} visible={!browseCards && activeRun === run.id && terminalKind === 'live' && mode !== 'chat'} onError={setNotice} />)}</Suspense>
                  {!currentRun && <div className="empty-state"><TerminalWindow /><h3>{native ? '保留 Agent 的原生交互' : '在桌面版连接真实 TUI'}</h3><p>{native ? '启动独立进程。切换会话或视图时，终端会在后台保留。' : '网页模式可阅读真实历史记录；PTY 进程由 Rust 桌面宿主管理。'}</p>{native && <button className="primary-button" onClick={openStart}><Play />启动 Agent</button>}</div>}
                </div>{currentRun && <div className="run-state"><i className={`state-dot ${currentRun.state}`} />{currentRun.state === 'running' ? '进程运行中' : `进程已退出 · ${currentRun.exit_code ?? 'unknown'}`}<span>PID {currentRun.pid ?? '—'} / {currentRun.id.slice(0, 8)}</span></div>}
              </div>
            </section>
          </div>
          {inspector && <aside className="inspector" id="inspector" aria-label="产物与来源"><header><h2>上下文与产物</h2><button className="icon-button" aria-label="关闭产物面板" onClick={() => patch({ inspector: false })}><X /></button></header><span className="eyebrow">ARTIFACTS / {session?.artifacts.length ?? 0}</span>{session?.artifacts.map((artifact) => <button className="artifact-card" key={artifact.id} onClick={() => setPicture(artifact)} disabled={!artifact.available}><Cube /><span>{artifact.name}<small>{artifact.available ? '本地图片 ↗' : artifact.reason}</small></span></button>)}{!session?.artifacts.length && <p className="inspector-note">来源未提供可识别的图片附件。不会通过扫描目录猜测产物。</p>}<dl><dt>AGENT</dt><dd>{session ? <span className="agent-identity"><AgentLogo agent={session.agent} size="sm" />{agentLabels[session.agent] || session.agent}</span> : '—'}</dd><dt>工作目录</dt><dd>{homePath(session?.cwd) || '未知'}</dd><dt>来源</dt><dd>{homePath(session?.source) || '—'}</dd><dt>原始会话 ID</dt><dd>{session?.native_id || '—'}</dd><dt>内容模式</dt><dd>Historical / read only</dd><dt>索引时间</dt><dd>{formatDate(session?.indexed_at || null)}</dd></dl>{session?.source && <CopyButton text={session.source} label="复制来源路径" />}</aside>}
        </div>
        <div className="archive-footer"><ChatCircleText /><span>历史记录，只读浏览。可复制命令到外部终端恢复；应用内新启动的终端独立运行。</span><span className="archive-label">ARCHIVE MODE</span></div>
        <footer className="statusbar"><span><i />LOCAL FIRST <b>/ {session?.agent.toUpperCase() || 'WORKSPACE'} / {terminalKind === 'live' && currentRun ? 'NATIVE PTY' : 'HISTORICAL'}</b></span><span>{native ? 'RUST · TAURI' : 'BROWSER PREVIEW'} · {runs.filter((run) => run.state === 'running').length} ACTIVE RUNS</span></footer>
      </main>
    </div>
    {notice && <div className="toast" role="status"><span>{typeof notice === 'string' ? notice : notice.text}</span><button className="icon-button" aria-label="关闭提示" onClick={() => setNotice('')}><X /></button></div>}
    {bootstrap.isError && <div className="connection-banner" role="alert"><WarningCircle /><span>{errorText(bootstrap.error)}</span><button onClick={() => void bootstrap.refetch()}>重试</button></div>}
    <DeleteSessionDialog target={deleteTarget} busy={deleteMutation.isPending} error={deleteMutation.error}
      close={() => setDeleteTarget(null)} confirm={(target) => { if (!deleteMutation.isPending) deleteMutation.mutate(target); }}
      restoreFocus={() => {
        if (deleteTrigger.current?.isConnected) deleteTrigger.current.focus();
        else searchRef.current?.focus();
      }} />
    <Modal open={dialog === 'sources'} onOpenChange={(open) => !open && setDialog(null)} title="数据源与覆盖情况" description="本地发现、增量索引。来源读取失败时保留已有快照。" wide><Sources sources={data?.sources ?? []} busy={scanMutation.isPending} scan={(paths) => scanMutation.mutate(paths)} /></Modal>
    <Modal open={dialog === 'settings'} onOpenChange={(open) => !open && setDialog(null)} title="关于 Harnession" description="Signal Observatory · Local-first Agent workspace"><div className="about-grid"><div><span className="eyebrow">KEYBOARD</span><p>Ctrl / ⌘ K · 搜索会话<br />Tab · 在控件间移动<br />← → · 切换阅读标签<br />Escape · 关闭对话框</p></div><div><span className="eyebrow">DATA BOUNDARY</span><p>扫描不修改原始数据，本地索引独立保存。<br />收集来源已保存的工具、思考和系统提示，默认折叠。<br />远程图片不会自动加载。</p></div></div><p className="inline-notice">{native ? '桌面模式支持真实 PTY。' : '当前为网页开发预览，连接本地 Python 索引服务。'}恢复原生会话和结构化实时 Chat 尚未启用。</p></Modal>
    <Modal open={dialog === 'start'} onOpenChange={(open) => !open && setDialog(null)} title="启动新的 Agent 运行" description="在选定工作目录启动独立进程；此操作不会恢复或重发历史对话。"><form onSubmit={(event) => { event.preventDefault(); startMutation.mutate(); }}><label className="field-label" htmlFor="start-agent">Agent / 终端</label><select id="start-agent" value={startAgent} onChange={(event) => setStartAgent(event.target.value)}>{installedAgents.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select><label className="field-label" htmlFor="start-directory">工作目录</label><input id="start-directory" value={session?.cwd ? homePath(session.cwd) : directory} disabled={Boolean(session)} onChange={(event) => setDirectory(event.target.value)} placeholder="/path/to/project" /><p className="form-hint">{session ? '使用此历史会话的工作目录，启动新的运行实例。' : '必须是本机已存在的目录。'}输入不会自动补充 Enter。</p><div className="modal-footer"><span>同一时间最多保留 8 个终端。</span><button className="primary-button" type="submit" disabled={!installedAgents.length || !(session?.cwd || directory) || startMutation.isPending}>{startMutation.isPending ? <SpinnerGap className="spin" /> : <Play />}{startMutation.isPending ? '启动中…' : '启动'}</button></div></form></Modal>
    <Modal open={Boolean(picture)} onOpenChange={(open) => !open && setPicture(null)} title={picture?.name ?? '图片预览'} description="来自此会话显式引用的本地附件。" wide>{artifactQuery.isPending ? <Loading label="读取图片…" /> : artifactQuery.isError ? <ErrorState error={artifactQuery.error} retry={() => void artifactQuery.refetch()} /> : artifactQuery.data && <div className="image-preview"><img src={artifactQuery.data.data_url} alt={picture?.name || '会话图片'} /></div>}</Modal>
  </div>;
}

function Loading({ label }: { label: string }) { return <div className="loading" role="status"><SpinnerGap className="spin" />{label}</div>; }
function ErrorState({ error, retry }: { error: unknown; retry: () => void }) { return <div className="error-state" role="alert"><WarningCircle /><p>{errorText(error)}</p><button className="text-button" onClick={retry}>重试</button></div>; }
