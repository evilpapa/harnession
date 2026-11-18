# 桌面工作台开发与验证

当前实现：Tauri 2 / Rust + React / TypeScript + Python sidecar。原始 Agent 数据保持只读，SQLite 索引与用户元数据独立保存。

## 启动桌面版

开发环境需要 Python 3.9+、uv、Node（使用项目锁文件）和 Rust。macOS 构建可使用 Xcode Command Line Tools（`xcode-select --install`）。Linux 构建需要 GTK3 / WebKitGTK 4.1。

仓库根目录：

```sh
uv sync --locked
cd apps/desktop
npm ci
npm run desktop:dev
```

开发命令会用 PyInstaller 构建本机 target 对应的 sidecar，再启动 Tauri 和 Vite。首次安装或编译需要下载依赖。桌面调试默认调用源码 Python gateway；可用 `HARNESSION_PYTHON` 指定解释器。正式 release 使用同目录的自包含 sidecar，不依赖系统 Python。

首次打开会读取已有索引；没有索引时显示引导。点击“发现本地会话”或列表上方扫描按钮建立结构化消息快照。数据源面板可保存自定义路径，显示缺失、部分失败、不支持和扫描时间。

索引位置沿用 CLI：`HARNESSION_HOME`、`XDG_DATA_HOME`、平台数据目录均继续生效。打开旧索引时，从已有会话的原始来源读取结构化记录，保留 rowid、会话 ID 和手动项目归属。原始来源不可用或无法识别时，继续展示旧版文本快照。点击扫描可重新发现来源和新增会话。

## 浏览器开发预览

终端一，在仓库根目录启动 loopback API：

```sh
uv run --locked python -m harnession.web
```

终端二：

```sh
cd apps/desktop
npm run dev
```

打开 `http://127.0.0.1:1420`。网页预览使用真实 API 和本机索引，但不提供 PTY。API 仅监听 loopback，校验 Host/Origin 和 JSON 请求类型，不开放 CORS。Vite 和 Tauri 开发版共用 1420 端口，切换启动方式时先结束已运行的 Vite。

如果需要演示或截图，使用隔离的合成来源，不修改真实索引：

```sh
uv run --locked python scripts/create-demo.py
uv run --locked python -m harnession.web --db .artifacts/demo/index.sqlite3
```

合成数据也经过真实解析器和索引服务，包含 Markdown、图片附件和 Pi 分支；不会调用模型。桌面端可通过设置 `HARNESSION_HOME` 为 `.artifacts/demo` 的绝对路径来使用同一索引。

## 构建 release

```sh
cd apps/desktop
npm run desktop:build
```

macOS 输出位于 `apps/desktop/src-tauri/target/release/bundle/` 下的 `macos/Harnession.app` 和 `dmg/Harnession_0.1.0_aarch64.dmg`（Apple Silicon）。可以直接打开 `.app`，或挂载 `.dmg` 后把 Harnession 拖入「应用程序」。安装包内的索引服务位于 `Harnession.app/Contents/MacOS/harnession-gateway`。macOS 最低版本配置为 13.0。

本机安装包使用 ad-hoc 签名。为让内置 PyInstaller sidecar 加载同样采用 ad-hoc 签名的 Python 动态库，本地打包关闭 Hardened Runtime。Developer ID 签名与公证需要另行配置，并在构建 sidecar 时为内嵌库使用一致的签名身份。[PyInstaller macOS 签名说明](https://www.pyinstaller.org/en/stable/feature-notes.html#macos-binary-code-signing)

从 Finder 启动时，Agent 检测、PTY 和索引服务会在继承的绝对 PATH 项之后补充 `~/.local/bin`、`/opt/homebrew/bin`、`/usr/local/bin` 和系统命令目录。只在自定义 shell 配置中添加的路径可从已配置的终端启动应用使用。

Linux `.deb` 的输出位置：

```text
apps/desktop/src-tauri/target/release/harnession-desktop
apps/desktop/src-tauri/target/release/harnession-gateway
apps/desktop/src-tauri/target/release/bundle/deb/
```

可直接运行 release 程序，分发时需要同时保留 sidecar，或使用生成的安装包。Python sidecar 按本机 Rust target 构建。需要其他架构或操作系统的包时，在对应环境重新构建。Linux 二进制的 glibc / WebKit 兼容性取决于构建环境，应在最早支持的发行版环境中制作分发包。

`cargo test`、开发运行与正式 release 的资源加载方式不同，正式包需要单独启动验证。Intel Mac 和 Windows 桌面包尚未实机验证。

## Codex 会话名称

Codex 会话名称读取本地元数据：优先使用版本化 `state_*.sqlite` 中 `threads.name` 的显式名称，其次使用 `session_index.jsonl` 中该 ID 最新的 `thread_name`，再使用 `threads.title`；没有名称元数据时保留来源标题或首条用户消息。SQLite 以只读模式打开，支持旧版缺少 `name` 列的表结构。打开已有索引时会刷新名称，后续扫描也会更新仅改名但对话文件没有变化的会话，消息 ID 与手动项目归属保持不变。

## 复制会话删除与恢复命令

打开会话后，右上角并排提供“复制删除命令”和“复制恢复命令”。终端图标可展开查看原始 session ID、目标、原生删除、单文件备用删除及完整恢复命令，并分别复制。应用仅生成文本，不执行这些命令，也没有删除或恢复 RPC。

Codex 原生 sessions 目录中的 UUID 会话优先使用 `env CODEX_HOME=... codex delete -- <session-id>`（需要支持 delete 的版本），配置到 sessions 的子目录时也能定位正确 home；Hermes 的 `state.db` 会话使用 `env HERMES_HOME=... hermes sessions delete -- <session-id>`。Codex 同时提供单文件备用删除；Claude Code、Pi 以及非原生 Codex 导入记录按原始 ID 对应的来源生成单个绝对路径的 `rm -- <file>`。Claude 的 `rm` 子命令清理后台运行/worktree 并保留对话，不能用于历史对话删除；Pi 没有独立的按 ID 删除 CLI 子命令。路径经过 shell 引用，不生成通配符、递归删除或共享数据库文件删除。

恢复命令：Codex 使用 `env CODEX_HOME=... codex [--cd <cwd>] resume -- <session-id>`；Claude 使用对应 `CLAUDE_CONFIG_DIR` 和 `claude --resume <session-id>`；Pi 使用 `pi --session-dir <root> --session <absolute-file>`，原生目录额外保留 `PI_CODING_AGENT_DIR`；Hermes 使用对应 `HERMES_HOME` 和 `hermes --resume <session-id>`。Claude、Pi、Hermes 有工作目录时先使用带引用的 `cd -- <cwd> && ...`。不添加新会话、fork、自动提交提示或绕过权限的参数。

来源缺失、路径越界、ID 无效、工作目录不可用，以及无法用原生 CLI 定位的自定义数据目录会显示不可用原因。命令作用于 Agent 来源；Harnession 已保存的索引快照仍保留，文件删除命令也不承诺清理 Agent 的其他缓存。复制恢复命令用于在外部终端继续会话；桌面内嵌 PTY 的原生恢复仍在后续范围。

2026-11-17 验证：Python 63 项、前端 5 项测试通过，TypeScript / production / Linux `.deb` 构建通过。使用四种 Agent 的独立合成来源验证原始 ID、profile、来源范围、shell 引用及禁用原因；release gateway 返回原生/文件删除选项和四种恢复命令。浏览器验证复制回调传入的文本、切换会话、1440/375px 布局及窄屏截图，未执行生成的删除或恢复命令；未将此记为原生 CLI 恢复端到端测试。

## 交互终端

桌面端点击“启动 Agent”：

- 检测 PATH 中的 Codex、Claude Code、Pi、Hermes 及系统 shell，仅展示可找到的程序。
- 有历史会话时使用其工作目录，启动**新的运行**，不恢复旧会话、不重发历史输入。
- 通过 `portable-pty` 管理进程，xterm.js 处理终端画面和输入，Tauri Channel 发送有序输出。
- 每个 run 独立缓存、独立序号。前端在 xterm 写入完成后确认消费，后端再推送下一块；每个 run 输出缓冲上限约 4 MiB，溢出会明确显示 gap。
- UTF-8/ANSI 以字节流传递，resize 同时更新 PTY；切换视图/会话不会重新启动进程或重发输入。
- 系统级快捷键和工作台搜索不会抢占终端内部 Ctrl+K；粘贴不自动追加 Enter。
- 最多保留 8 个终端。结束动作请求 portable-pty 停止已启动进程，退出状态由 wait 观察；Unix 底层使用 SIGHUP，Windows 使用进程句柄结束。复杂子进程树和忽略信号的程序需要进一步验证。
- 停止进程后可关闭终端；关闭阅读面板不会停止进程。应用退出时请求停止仍在运行的子进程。

历史“记录视图”显示结构化消息的终端排版，明确标注非原始终端回放。对于非结构化 CLI 输出，不将自然语言推断为确定的 Agent 思考或审批状态。

## 已实现与后续范围

已实现：项目/Agent 筛选、关键词检索、分页、消息定位、Markdown/代码/表格、显式图片附件、来源覆盖提示、Pi 父子关系提示、Chat/TUI/并排、聚焦布局、响应式列表与详情、数据源配置、真实 PTY 和进程列表，以及已记录的工具调用/输出、思考、系统/开发者提示。

技术记录在 Chat 和 TUI 记录视图中默认折叠，展开后才挂载正文，长内容可继续展开并完整复制。来源定位和显式调用 ID 保留，检索覆盖技术正文，列表摘要显示最后一次用户提问。同一原始记录拆出的内容块使用独立稳定 ID 和连续分页顺序。仅收集来源实际保存的内容；加密或脱敏思考只展示不可读提示。打开桌面版或执行扫描时会按当前解析器版本补采未变化的旧文件。

连续超过 10 条思考、工具调用、工具输出或系统/开发者提示，会在 Chat 和 TUI 中合并成默认关闭的“过程记录”。外层显示分类数量、失败数和搜索命中数，展开后才挂载逐条折叠控件；底部收起按钮会把键盘焦点与视口带回分组标题。普通对话及独立会话指令标签中断分组，分页加载先合并已读事件再分组，追加记录时保留首条记录作为分组身份。数量反映当前已加载内容，后续页读取后继续更新；检索定位从命中处开始阅读时遵循同一规则。

会话指令使用独立的 `instruction` 类型，支持消息开头的 `AGENTS.md instructions`、`Role` 标题，以及明确成对的 INSTRUCTIONS、role、environment_context、skills_instructions、permissions 等标签。Chat / TUI 显示圆角虚线标签条，区分工具输出、思考和系统提示，正文默认不挂载；展开后可阅读和复制。正常提问里的词语提及、引用、代码围栏以及普通正文引出的 Role 标题保留为对话。混合记录拆分后，原始消息身份与图片归属优先保留给普通提问，所有内容仍可检索。

以 `Base directory for this skill: <路径>` 开头的原生技能消息归入 `instruction_category=skill`，标签显示“技能 · 目录末级名称”，完整路径仅在展开原文时展示。技能内的多级标题和代码围栏保持在同一折叠条中，连续加载的多个技能文档各自显示独立标签；普通提问、引用和代码示例里的标记不触发分类。此类标签独立于“过程记录”大分组，仍可检索、定位和复制原文。

解析器版本 4 在打开旧索引时通过已有的来源补采流程升级；来源失败时继续保留旧快照。列表按记录顺序查找最后一次非空普通用户提问，排除指令和工具结果；同一来源记录的普通片段合并，截取前 180 字符，无提问显示“暂无用户提问”。列表标题继续遵循来源名称策略。

图片支持明确引用的 PNG/JPEG/WebP/GIF，本地文件须位于来源工作目录内；读取时重新校验规范路径、类型、文件签名和 8 MiB 上限。远程图片不自动加载，SVG/HTML 不作为带 IPC 权限的可执行页面展示。

后续范围：原生会话恢复、结构化实时 Chat、可选择的 Pi 活动分支、Diff/代码产物识别、终端持久录制与跨重启恢复、全量虚拟化、全文索引、文件监控，以及三平台性能和安装验证。当前不提供这些能力的假按钮或推测状态。

## 验证命令

```sh
uv run --locked python -m unittest discover -s tests -v
cd apps/desktop
npm run build
cargo test --manifest-path src-tauri/Cargo.toml --lib
cargo clippy --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
```

Python 测试覆盖旧数据库升级、消息边界、来源只读、稳定 ID、分页/定位、快照更新、损坏来源保留、图片签名和路径越界、协议错误以及自定义路径持久化。Rust 测试覆盖真实 Python JSONL 通信和原生 PTY 的中文、ANSI、resize 与退出码。

诊断桌面索引通信时，可设置 `HARNESSION_TRACE=1`。它只输出完成的 API 方法名，不记录查询、路径、消息或认证信息。

浏览器交互验证使用独立合成索引；真实 Codex、Claude 和 Pi 来源另外进行了只读解析抽样，未把私有消息写入文档或截图。截图与临时验证数据位于 `.artifacts/`，不作为源代码发布。

## 2026-11-15 至 2026-11-16 验证结果

- Python 35 项测试通过；原有 CLI 的 19 项用例全部保留并通过。
- Rust 3 项测试通过：原生 PTY、Channel 消费确认/序号补发、真实 Python JSONL 通信；Clippy 使用 `-D warnings` 通过。
- TypeScript 检查与 Vite production 构建通过。Markdown 和终端按需加载，主入口约 378 kB，gzip 约 114 kB。
- 浏览器验证：Chat/TUI/并排、项目/Agent 筛选、搜索定位、图片 API 与预览焦点、上下文抽屉、来源路径保存和扫描、Pi 覆盖提示、聚焦布局。
- 1440 / 1280 / 1024 / 768 / 390px 下实际测量均无整页横向溢出；390px 验证了列表进入详情。
- Linux release 和约 14.5 MiB 的 `.deb` 均构建成功；包内确认包含桌面主程序、自包含 gateway 和桌面启动入口。
- 停止 Vite 与 Python HTTP 开发服务后，独立启动 release；通过真实 Tauri IPC 完成 `bootstrap`、`list_sessions`、`get_session`、`read_events`。这验证正式包资源与 sidecar 不依赖开发服务器。
- 本机桌面自动化提供者未能枚举 Tauri 窗口，因此没有把原生窗口像素、点击或完整 Agent CLI 操作记为已验证；浏览器截图和交互测试与原生协议/PTY 测试分别记录。
- 真实 Codex / Claude / Pi 来源各抽取一个文件进行只读解析，分别识别 52 / 94 / 20 条可见事件；不据此声称覆盖这些来源的所有版本。

当时尚未完成原生窗口端到端输入、完整子进程树停止、Windows/macOS、读屏、百万级数据和安装升级测试。原生会话恢复、结构化实时 Chat 和其余规划项继续按阶段实现。

## 2026-11-17 技术记录验证

- Python 57 项、前端状态测试 5 项、Rust 3 项测试通过，TypeScript 与 Vite production 构建通过。
- 新增构造样本覆盖 Codex / Claude Code / Pi / Hermes 的系统提示、思考、工具调用和输出；包括单行多内容块、逐条分页、全文子串检索、工具关联、图片附件、空结果、长输出、加密占位及旧索引补采。原始来源字节和修改时间保持不变。
- `python3 scripts/create-demo.py --directory .artifacts/technical-demo` 可生成独立合成索引。浏览器确认 Chat / TUI 默认关闭技术折叠条，关闭时正文不挂载；展开一条不影响其他条，24,054 字符输出可展开完整文本，工具输出中的 HTML 标签作为文字显示。搜索命中技术记录后仍保持折叠，键盘 Enter 可展开。
- 1440px 和 375px 布局测量无整页横向溢出，375px 的技术摘要无横向溢出。浏览器截图接口持续超时，本轮不记为像素截图或原生窗口端到端验证。
- Linux release / `.deb` 重建完成（约 14.64 MiB）。包内包含主程序、gateway 与桌面启动入口；自包含 release gateway 使用合成来源通过扫描、搜索、读取新消息类型和列表摘要检查，无需 Python 开发服务。

## 2026-11-18 macOS 验证

- Apple Silicon、macOS 27.2.1。开发模式成功加载合成索引。正式 `.app` 和 `.dmg` 构建成功，镜像校验及复制后应用的 `codesign --verify --deep --strict` 校验通过。
- 从 DMG 复制应用后用 LaunchServices 启动。在系统 PATH 下完成索引初始化、会话列表和逐消息读取，使用包内 Python 服务。
- 原生 WKWebView 自动化通过 `⌘K`、搜索定位、图片解码、并排阅读和 Agent 检测。系统 shell 的 PTY 验证了粘贴等待回车、中文输出、退出码与关闭；使用合成会话，未启动真实 Agent 会话。
- Python 63 项、前端 5 项、Rust 4 项通过。TypeScript、Vite production、Rustfmt 和 Clippy 通过。系统剪贴板实写、其他 macOS 版本与 Intel Mac 尚未实测。

## 2026-11-18 指令标签与列表摘要验证

- Python 74 项、前端状态测试 5 项通过；TypeScript 检查、Vite production、Linux release 和 `.deb` 构建通过。
- 新增用例覆盖 AGENTS.md / Role 与显式上下文标签、同类标签嵌套、连续标签、代码围栏保护、普通提问保留、附件归属、来源消息身份，以及 Codex / Pi / Hermes 消息分类。旧索引重新打开后自动升级到解析器版本 3，保留会话 ID、名称覆盖和原始来源。
- 合成索引包含 6 个会话。浏览器确认 Chat / TUI 的 AGENTS.md、Role、环境上下文、技能指令默认折叠，关闭时正文不挂载；展开一条不会展开其他条，完整原文复制按钮返回“已复制”。搜索指令正文可命中，命中项仍保持折叠。
- 列表摘要显示最后一次普通用户提问，忽略末尾的助手回复、指令和工具结果；同条记录中被上下文分隔的普通片段合并，无提问时显示占位文字。
- 1440px 截图已检查；375px 的列表、详情和指令摘要测量无横向溢出。此轮使用浏览器验证界面，没有新增原生窗口端到端验证。
- 自包含 release gateway 通过 JSONL 协议读取上述合成索引，确认摘要、4 类指令标签和普通消息正确；来源文件的字节与修改时间保持不变。更新后的 Linux 安装包约 14.65 MiB。

## 2026-11-18 过程记录分组验证

- 前端 9 项测试通过，覆盖 10 / 11 条边界、用户与助手正文边界、独立指令标签、混合技术类型、顺序保留及跨页追加时的分组身份。TypeScript、Vite production、Linux release / `.deb` 构建通过。
- `python3 scripts/create-demo.py --directory .artifacts/grouped-demo` 生成 7 个合成会话，新增会话含 11、10、65 条连续过程记录。浏览器确认 11 条默认收进一个分组，10 条保持独立；65 条分组分页时从 33 条增长到 65 条，并保留已展开状态。
- Chat / TUI 外层折叠时不挂载子记录，展开外层后逐条内容仍默认折叠。展开一条可读取原文，底部收起会卸载子记录，并将键盘焦点带回标题。Enter 可操作外层分组。搜索命中显示分组命中数量，默认保持折叠。
- 1440px 截图已检查；375px 下 Chat / TUI 的外层分组、内层记录在展开和收起时均无整页或摘要横向溢出。此轮为浏览器交互验证，没有新增原生窗口端到端验证。

## 2026-11-18 原生技能说明折叠验证

- Python 78 项、前端 9 项测试通过，TypeScript、Vite production 和 Linux release / `.deb` 构建通过。新增用例覆盖完整技能 Markdown、连续技能文档、POSIX / Windows / UNC / `~` 路径、代码和引用保护、附件归属、摘要排除，以及旧版本 3 索引自动补采。
- 合成演示新增 Claude Code 技能消息，使用 `Base directory for this skill: /demo/.claude/skills/codebase-memory` 标记。浏览器确认 Chat / TUI 显示“技能 · codebase-memory”，默认关闭且不挂载正文；展开可读全部标题与代码示例，复制返回“已复制”。搜索技能正文可命中，保持折叠，列表摘要仍显示真实用户提问。
- 375px 下 Chat / TUI 的技能摘要及展开正文无整页横向溢出。浏览器交互验证与原生窗口端到端验证分开记录，本轮未新增后者。
- 更新后的自包含 release gateway 使用合成旧索引完成版本 3 → 4 升级，保留会话 ID，返回正确技能标签和用户提问摘要，来源文件字节与修改时间均保持不变。Linux 安装包约 14.66 MiB。
