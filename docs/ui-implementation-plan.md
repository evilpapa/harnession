# Harnession UI 实现规划 v1

日期：2026-11-15。状态：阶段 A/B/C 的首个可运行切片已实现；具体能力、运行方式与剩余范围见 [桌面开发说明](desktop-development.md)。本文保留完整目标规划，阶段验收不能仅依据原型或代码存在来判断。

## 1. 产品定位与本次假设

把 Harnession 从本地会话索引扩展为 **以项目为中心的 Agent 会话工作台**：找到一段工作记录，在 Chat 中读懂上下文，在 TUI 中使用 Agent 原生界面，在产物面板查看结果。

本次将“agent 绘画内容”同时覆盖为两类需求：

- 对话与执行内容：用户消息、助手回复、可识别的工具事件、代码及结果。
- 绘画与其他产物：图片、SVG、Markdown、代码、Diff；来源没有附件信息时明确显示缺失。

默认保持本地优先。历史浏览和启动 Agent 是两个独立能力；浏览历史不会自动启动进程、恢复会话或调用模型。

## 2. 规划开始时的基线与关键缺口

以下为规划开始时核对 `README.md`、`docs/requirements.md`、`harnession/core.py`、`harnession/__main__.py` 和 `pyproject.toml` 得到的基线；现有实现进展以桌面开发说明为准：

- 当前为 Python 标准库 CLI + SQLite；已有 Codex、Claude、Pi、Hermes 来源适配，OpenCode 显示尚未实现。
- `parse_jsonl()`、`scan_hermes()` 将用户/助手可见文本合并为 `sessions.content`；没有保留可靠的逐消息边界、逐消息时间、附件关系或终端字节流。
- `scan()` 管理来源扫描状态，`save()` 写入索引；`overrides` 保留手动项目归属。CLI 查询直接使用 SQL。
- 当前没有可复用的结构化 GUI 协议，没有 PTY 会话管理，也没有真实 Agent 运行状态采集。
- Pi 当前按文件记录顺序汇总所有分支。UI 不能把这种汇总标为“当前分支”，也不能把模型内部上下文当作可见消息。

**实现前置条件：先建立结构化消息读取，再实现高质量 Chat。不能按 `user:` 字符串切割旧 content，用户正文也可能包含这个字符串。历史终端画面无法从纯文本索引准确重建。**

代码图用于定位调用链；覆盖检查提示部分源码 metadata_changed，文档目录被索引排除，因此上述判断已经直接阅读磁盘源文件核对，而非仅依赖图索引。

## 3. 视觉方向：Signal Observatory

关键词：精密、安静、可观察、持续工作中的科技感。

### 视觉语言

- 主背景近黑石墨蓝，面板用三层明度区分，细边框建立秩序。
- 冰青表示当前焦点与交互；荧光绿表示成功或已连接；琥珀表示需要注意。
- 稀疏网格、局部光晕、细小序号和终端排版形成科技感。光晕只用于焦点、品牌和状态。
- 中文用本地系统黑体，英文 UI 可使用随包分发的 IBM Plex Sans，代码使用 JetBrains Mono。确认字体许可证后随包提供，不依赖运行时字体网络请求。
- 正文清晰、边界明确；每个页面有一个主要动作。时间、计数和状态有真实数据来源。

### 建议 Token

| Token | 值 / 规则 |
| --- | --- |
| `surface.canvas` | `#080D14` |
| `surface.panel` | `#0E1520` |
| `surface.raised` | `#151F2D` |
| `border.subtle` | `#263448` |
| `text.primary` | `#EDF4FC` |
| `text.secondary` | `#A6B5C8` |
| `accent.focus` | `#79E2F2`，搭配深色文字 |
| `status.success` | `#CBF28A`，与“已索引 / 已连接”等文字共同出现 |
| `status.warning` | `#F4C785` |
| `status.error` | `#FF9FA9` |
| 字号 | 正文 14–16px；元信息 12–13px；会话标题 20–24px |
| 间距 | 4 / 8 / 12 / 16 / 24 / 32px |
| 圆角 | 容器 12px；控件 8px；状态标记胶囊形 |
| 动效 | 控件 120–160ms；面板 180–240ms；只改变 opacity/transform |

Token 是设计起点，最终需测量组合后的对比度：正文 ≥4.5:1，关键图形/焦点 ≥3:1。支持 reduced-motion；不使用持续扫描线和闪烁正文。若后续提供浅色主题，需要独立设计和验证。

设计技能的通用结果包含 FAQ Landing 布局，和本产品不匹配，未采用；采用开发工具分类对应的深色、高密度、终端导向建议，并据产品目标重新设计信息结构。

## 4. 信息架构与界面

### 主工作台

桌面基准 1440 × 960。结构从左至右：

1. **导航轨道，56px**：工作台、搜索、数据源、设置。文字 tooltip 与键盘可达名称齐全。
2. **项目栏，约 208px**：项目、会话数、收藏、未归类；Agent 是过滤器，而不是唯一导航。
3. **会话栏，约 300px**：标题、来源 Agent、更新时间、内容摘要、已知状态；支持关键词与来源筛选。
4. **内容区，剩余宽度**：标题和来源信息，Chat / TUI / 并排切换，底部上下文状态。
5. **可折叠检查器，约 280px**：产物、来源定位、工作目录、分支与覆盖情况；默认折叠。

双击会话栏标题可聚焦内容，折叠项目/会话栏；宽度不足时优先隐藏检查器，随后把项目栏变为抽屉。小于 900px 使用列表→详情的单区域导航；小于 600px 并排切换为顺序阅读。拖动分隔条同时提供键盘调整尺寸和重置布局操作。

首页优先展示最近工作的项目和会话。概览统计使用实际索引数据；仅有历史记录时不显示“正在运行”“等待输入”或推测出来的进度。

### Chat

- 使用有角色标识的阅读流；正文优先，模型、时间和来源作为次级信息。
- 渲染 Markdown、代码块、表格与附件；长代码折叠，复制与展开可键盘操作。
- 工具调用、工具输出、思考和系统/开发者提示默认显示折叠条，包含类型、工具名、时间和字符数；展开后按文本渲染，可复制完整内容。调用与结果仅通过来源显式的关联 ID 关联，保持原始记录顺序。
- 图片支持缩略图、尺寸、来源、放大；文件缺失显示占位与原因。
- 搜索结果可以跳转到稳定的消息 ID，而非仅打开会话顶部。
- 只读历史底部显示“历史记录”；实时 Chat 输入框仅在适配器声明并验证结构化交互能力后启用。
- 流式追加只在用户接近底部时自动滚动；用户上翻后显示“有新内容”，不抢夺阅读位置。
- 收集来源实际保存的思考与系统提示，默认折叠；仅有加密或脱敏占位时标注原文不可读，不推断或解密。认证配置不作为独立数据源采集。

### TUI：必须区分的三种状态

| 状态 | 数据来源 | 允许行为 | UI 文案 |
| --- | --- | --- | --- |
| 记录视图 | 结构化历史消息 | 搜索、复制、跳转 | “终端风格记录 · 非原始终端回放” |
| 交互终端 | 本应用启动或已验证可连接的 PTY | 原生 Agent 输入、快捷键、窗口缩放 | “已连接 · 交互终端” |
| 终端回放（后续） | 本应用实际采集的输出流、尺寸与时间 | 播放、暂停、定位；只读 | “已录制终端 · 只读回放” |

交互终端使用真正的终端模拟器，不用 `<pre>` 代替 ANSI、光标和 alternate screen。核心行为包括：

- UTF-8、宽字符、中文输入法、复制选择、粘贴、Ctrl+C、终端鼠标事件。
- resize 同时更新前端终端网格和后端 PTY 的行列数；隐藏面板恢复时重新测量。
- 每个运行实例有唯一 run ID、项目、Agent、PID、启动参数、退出状态。
- 切换 Chat / TUI 或切换会话不会重新启动进程、重复提交输入或清屏。
- 关闭视图和结束进程是两个动作。支持后台保留；停止时先尝试正常中断，退出结果以进程观察为准。
- 仅有来源文件无法证明某个外部终端仍存在；默认不能“接管”已经在其他应用启动的 Agent。
- 输入粘贴不自动补 Enter；快捷键明确归属终端或工作台，避免拦截 Agent 原生按键。

如果用户希望 Harnession 自身也能在纯终端运行，可另建 Ratatui 客户端共享 DTO；这不是桌面内嵌 Agent TUI 的必要依赖，排在本轮桌面能力之后。

### 并排阅读

Chat 与 TUI 左右并排，可调宽度。共享会话身份与运行身份，分别保留滚动位置。

消息、工具事件和终端片段只有在存在显式关联 ID 时支持交叉定位；不要把相近时间当作准确映射。终端只提供非结构化输出时，Chat 显示“暂无结构化同步”，仍可阅读历史消息。

### 辅助界面

- 全局搜索：输入关键词 → 跨项目结果 → 高亮上下文 → 定位消息。
- 数据源：路径、扫描时间、成功/部分失败/缺失/不支持、错误详情、重试。
- 新建/恢复：先选择 Agent 和项目，仅展示适配器已经验证的能力。
- 无会话：引导扫描；来源缺失：可配置路径；解析失败：保留旧记录并显示过期状态。
- 断连/退出：保留已接收内容，显示退出码或断连原因；重试不会自动重发上一条消息。
- 产物抽屉：按类型筛选；点击图片放大，代码/Diff 使用专门查看器，显示来源可用性。

## 5. 技术选型

**建议：Tauri 2 + Rust 桌面宿主 + React / TypeScript 界面，初期复用 Python 解析核心。**

| 层 | 选择 | 理由与边界 |
| --- | --- | --- |
| 桌面容器 | Tauri 2 | 集成本地 Rust 服务和 Web UI；系统 WebView 差异需要实测 |
| 界面 | React + TypeScript + Vite | 适合消息、终端、分栏与复杂状态组件组合；不需要 SSR |
| 样式 | CSS variables + Tailwind；Radix primitives | Token 驱动；对话框、菜单、Tabs 使用可访问语义组件 |
| UI 状态 | Zustand + TanStack Query | 分离布局/选择状态和索引查询缓存；不把终端字节写入全局 React 状态 |
| 终端 | xterm.js + fit addon | 字节输入、键盘事件与 resize 对接 PTY；WebGL addon 在实测后按需启用 |
| 进程 | Rust `portable-pty` 候选 + Tokio 调度 | 需要验证 Linux/macOS PTY、Windows ConPTY、退出与进程树管理 |
| 消息通道 | Tauri Commands + Channel | Commands 用于请求，Channel 用于有序增量；业务层仍实现序号与恢复 |
| 存储 | 现有 SQLite → 版本化迁移；后续可用 rusqlite | 使用稳定会话 ID，增量消息分页，不依赖临时 rowid 作为界面标识 |
| 文件监控 | notify 候选 + 去抖 + 定期补扫 | 文件变化仅是重新读取触发器，不能当作 Agent 正在工作的证据 |
| 首期适配器 | Python sidecar | 将现有解析能力包装为结构化 JSONL RPC；不解析 CLI 人类可读输出 |

不建议此时把前后端都重写为 Rust。Rust 负责桌面、进程和事件基础设施，Web UI 负责复杂内容呈现，既能保留技术方向，也减少第一版交付风险。

官方文档核查入口列于文末；最终依赖版本应在实际开始编码时选定并锁定。

## 6. 架构与能力模型

```text
React 工作台
  ├─ Project / Session / Search / Source views
  ├─ Chat renderer / Artifact viewer
  └─ xterm.js（按 run_id 管理终端实例）
             │ Commands / Channels
Rust Tauri 应用服务
  ├─ Index gateway ── JSONL RPC ── Python sidecar ── SQLite / 来源适配器
  ├─ Run manager ── PTY ── Agent CLI
  ├─ Agent capability registry
  └─ Event router / 本地文件访问 / 生命周期管理
```

**能力探测是产品行为的一部分：**

```ts
type AgentCapabilities = {
  history: boolean;
  toolEvents: boolean;
  artifacts: boolean;
  spawnPty: boolean;
  resumeNativeSession: boolean;
  structuredChat: boolean;
  branches: boolean;
};
```

每个能力附带 `supported | unsupported | unverified` 和原因、已验证 Agent 版本。以上布尔值是界面读取的派生结果；unverified 不视为 supported。来源可索引不等于该 Agent 可恢复，也不等于可结构化聊天。

历史 adapter 和 runtime adapter 分开：前者读取记录，后者启动、恢复和交互。不要从终端画面的自然语言反推“正在思考”“等待批准”作为确定状态。对于不提供结构化交互协议的 CLI，保留真实 TUI，Chat 仅展示可验证的历史内容。

**单写入者原则：**过渡期由 Python gateway 串行处理索引写入和迁移，Rust 不同时改表；现有 CLI 写入也必须接入同一锁机制。PTY 录制先写独立日志。Rust 接管数据库之后再移除 Python 写入口。打包 sidecar 必须包含运行环境，不假设用户机器安装了 Python。

## 7. 数据模型与接口草案

保留旧 `sessions` 与 `overrides`，通过带版本号的事务迁移新增以下结构：

| 实体 | 关键字段 |
| --- | --- |
| Session | `id`、legacy key、agent、native_id、source、cwd、effective_project、title、indexed_at、parse_status |
| Event | `id`、session_id、source_event_id、seq、parent_event_id、branch_id、kind、role、timestamp（可空）、payload、source_locator |
| Artifact | `id`、session_id、event_id、kind、mime、local_ref、size、availability |
| Run | `id`、session_id（可空）、agent、cwd、state、pid、started_at、ended_at、exit_code |
| TerminalChunk | `run_id`、seq、monotonic_time、stream、bytes；尺寸变化作为有序事件存储 |
| SourceState | status、last_success_at、last_attempt_at、errors、coverage |

约定：

- 历史 Session 使用稳定的 opaque ID，并保存旧 key 映射；同一原始 ID 出现在多个来源时不自动合并。
- Event 优先使用来源记录 ID；缺失时按来源代际和记录位置生成稳定标识，文件重写要检测并重新关联，不能仅靠内容 hash 去重。
- 保留来源原始顺序；没有时间就显示未知，不分配虚假时间。工具事件依赖来源实际存在。
- Pi 保存父子关系与分支，无法确定活动路径时 `active_path = null`；不要把所有分支显示成一个确定的连续对话。
- 原始记录仍是只读来源，新增索引只是派生数据。升级时优先重新解析来源；来源丢失时保留旧 content，并标记“旧版文本记录”。
- 缩略图与缓存存入应用数据目录。来源没有附件关系时，不遍历整个项目猜测“Agent 生成了哪些文件”。

建议 IPC：

```ts
list_projects({ cursor, limit })
list_sessions({ projectId, agent, query, cursor, limit })
get_session({ sessionId })
read_events({ sessionId, cursor, limit, branchId })
search_events({ query, filters, cursor })
scan_sources({ sourceIds })
get_capabilities({ agent })
spawn_run({ agent, projectId, resumeSessionId? })
subscribe_run({ runId, afterSeq?, channel })
write_pty({ runId, bytes })
resize_pty({ runId, cols, rows })
stop_run({ runId })
read_artifact({ artifactId })
```

事件信封包含 `protocol_version`、`subscription_id`、`run_id/session_id`、`seq`、`kind`、`payload`；索引批次额外带 `index_generation`，用于发现分页期间的数据变化。

流式路径分为结构化 event 和原始 terminal bytes。终端流按字节连续解码，不能截断 UTF-8/ANSI 后以文本拼接。后端有有界缓冲，前端按小批次写入终端，用写入完成回调推动消费；不为每个字符触发 React render。

订阅先注册再返回快照及序号水位；事件按 seq 去重。断线从 lastSeq 请求补发，日志已裁剪时明确通知 gap 并提供新快照。重连只恢复输出订阅，不重试输入。输入交给 PTY 成功也只意味着写入完成，不能声称 Agent 已经处理。

## 8. 本地内容渲染边界

- Markdown 默认不执行 HTML，代码与工具输出作为文本渲染；外部链接交给受控打开操作。
- 本地附件通过 ID 映射读取，校验真实路径与允许的来源范围；不接受前端传来的任意绝对路径读取命令。
- Agent 生成的 SVG/HTML 作为图片、文本或隔离预览；不在带 Tauri IPC 权限的主页面直接执行。
- Tauri capability 按窗口和命令收敛。命令执行由后端 Agent registry 构建参数数组，不把消息正文拼进 shell。
- 交互终端来自用户主动启动/恢复的运行实例。纯浏览会话不获得进程写入权限。
- 录制终端默认关闭；启用后明确展示保存位置和保留策略。远程图像/模型内容不自动联网拉取。

这些约束直接决定用户能否安全预览来源内容，不额外增加与任务无关的批准流程。

## 9. 推荐目录

```text
apps/desktop/
  src/
    app/                 # 路由、布局、Token、错误边界
    features/
      projects/
      sessions/
      chat/
      terminal/
      artifacts/
      search/
      sources/
    shared/
      ipc/               # DTO、Commands、Channel 订阅
      ui/
  src-tauri/
    src/
      commands/
      index_gateway/
      runtime/
      adapters/
      artifacts/
    capabilities/
packages/protocol/       # 协议版本、JSON schema、fixture
harnession/              # 现有核心；新增 structured reader 与 RPC gateway
docs/
  ui-implementation-plan.md
  ui-prototype.html
```

原型独立于正式应用，没有引入上述目录、依赖或桌面运行时。

## 10. 分阶段实施与验收

估时仅供排期：熟悉项目的单人约 17–28 个工作日；Agent 协议、系统 WebView 和安装包差异可能改变范围。每阶段完成可运行切片，不把全部风险留到最后。

| 阶段 | 工作 | 预计 | 完成证据 |
| --- | --- | --- | --- |
| A · 内容底座 | DTO、逐消息 reader、稳定 ID、旧索引迁移策略、JSONL RPC；同时验证一种真实 PTY CLI | 4–6 日 | 真实来源能输出可定位消息；消息正文包含 `user:` 也不会错分；PTY 中文/resize/退出可用 |
| B · 可用工作台 | Tauri 壳、Token、项目/会话列表、来源状态、Chat、搜索、分页与布局 | 5–8 日 | 使用真实本机索引完成查找→打开→定位；缺失/错误状态可见 |
| C · TUI 和产物 | xterm、进程管理、Chat/TUI/并排、图片与代码预览；一种已验证 Agent 的启动/恢复 | 5–8 日 | 切换视图不重启进程；停止状态准确；来源不足时不伪造 Chat 同步 |
| D · 交付质量 | 大量数据、键盘、无障碍、三平台构建与实机冒烟、安装升级 | 3–6 日 | 指标记录、目标平台兼容矩阵、可安装包与升级演练 |

首个桌面可发布切片包含：项目/会话索引 + 高质量只读 Chat + 真正可用的一种 Agent PTY + 三种阅读布局。其余 Agent 的历史支持继承现有范围，交互能力逐一通过测试后开放。

后续：多会话终端分屏、录制回放、结构化实时 Chat、跨 Agent 交接、Ratatui 客户端。3D 拓扑、远程设备与云端同步不进入第一版关键路径。

## 11. 验证与性能目标

以下为待测目标，不是当前性能承诺；基准机器、来源大小与事件规模需写入测试记录。

- 参考数据集：1 万会话、单会话 1 万消息、至少一个 10MB 工具输出，以及三平台路径/Unicode fixture。
- 已完成索引时，会话首屏目标 ≤500ms；常用本地搜索 P95 目标 ≤300ms。
- 会话与消息都分页/虚拟化，长工具输出折叠并按需读取；终端限额 scrollback。
- 流式写入时不持续占用主线程，目标交互反馈 ≤100ms，使用真实 PTY 输出压力测试。
- 覆盖多字节拆包、ANSI 跨 chunk、alternate screen、中文宽字符/输入法、resize 风暴、进程异常退出、订阅恢复与缓冲溢出。
- 适配器用脱敏 fixture 验证各已支持版本；损坏数据保留旧索引，混合分支明确标记。
- UI 测试覆盖选择会话、筛选、消息定位、三视图切换、后台进程保留、退出与缺失产物。
- 布局检查 1440 / 1280 / 1024 / 768 / 390px；读屏、焦点和 reduced-motion；所有数据状态不只依赖颜色。
- Windows、Linux、macOS 各自完成构建与安装冒烟后才标记兼容；真实 PTY 与恢复能力按 Agent / 版本 / OS 分列验证。

## 12. 原型与正式实现的区别

`ui-prototype.html` 是无第三方依赖的交互式视觉原型，包含项目与 Agent 过滤、会话切换、关键词检索、Chat/TUI/并排、产物抽屉与 SVG 示例放大。所有记录都是合成示例，所有终端输出都是排版示意。

原型没有读取实际会话、启动进程、连接模型或执行命令；交互终端入口会明确说明工程阶段，历史内容不提供发送功能。

## 13. 官方技术核查入口

1. Tauri Rust Commands / Channel：https://v2.tauri.app/develop/calling-rust/
2. Tauri sidecar 打包：https://v2.tauri.app/develop/sidecar/
3. Tauri capabilities：https://v2.tauri.app/security/capabilities/
4. xterm.js Terminal API：https://xtermjs.org/docs/api/terminal/classes/terminal/
5. portable-pty API：https://docs.rs/portable-pty/latest/portable_pty/

第一版实现时从上述官方资料选定 API 和锁定版本，并以真实程序验证，不能把“库具有某 API”视为“本项目已实现该能力”。

## 14. 本次原型验证记录

2026-11-15：

- 通过 Orca 内嵌浏览器验证了 9 项交互：TUI 切换、并排、产物抽屉、放大预览与焦点、Escape 关闭、Agent 筛选、关键词搜索、空结果、项目筛选。
- 在 1440 / 1024 / 768 / 390px 下读取实际布局尺寸，页面 `scrollWidth` 均未超过可用宽度。
- Orca 截图两次返回页面未绘制帧的超时错误；随后使用本机 Chromium headless 输出并人工核对桌面截图，见 `ui-prototype-desktop.png`。
- 本次只验证原型，不代表完成正式应用的性能、读屏、三平台、真实数据或 PTY 测试。
- 已读取文末五项官方文档，核对 Commands/Channel、sidecar、capabilities、终端 API 与跨平台 PTY 的选型依据；依赖版本尚未锁定。
