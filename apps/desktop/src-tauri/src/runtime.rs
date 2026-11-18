use base64::{engine::general_purpose::STANDARD, Engine};
use portable_pty::{native_pty_system, CommandBuilder, MasterPty, PtySize};
use serde::Serialize;
use std::collections::{HashMap, VecDeque};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::{
    atomic::{AtomicU64, Ordering},
    Arc, Condvar, Mutex,
};
use std::time::Duration;
use tauri::ipc::Channel;
use uuid::Uuid;

const BUFFER_BYTES: usize = 4 * 1024 * 1024;

#[derive(Clone, Serialize)]
pub struct Agent {
    pub id: String,
    pub label: String,
    pub installed: bool,
    pub resume: bool,
    pub structured_chat: bool,
}

#[derive(Clone, Serialize)]
pub struct RunInfo {
    pub id: String,
    pub session_id: Option<String>,
    pub agent: String,
    pub cwd: String,
    pub pid: Option<u32>,
    pub state: String,
    pub exit_code: Option<u32>,
}

#[derive(Clone, Serialize)]
pub struct RunEvent {
    pub run_id: String,
    pub seq: u64,
    pub kind: String,
    pub data: String,
    pub exit_code: Option<u32>,
}

struct Log {
    items: VecDeque<RunEvent>,
    bytes: usize,
    seq: u64,
}

pub struct Run {
    pub info: Mutex<RunInfo>,
    master: Mutex<Box<dyn MasterPty + Send>>,
    writer: Mutex<Box<dyn Write + Send>>,
    killer: Mutex<Box<dyn portable_pty::ChildKiller + Send + Sync>>,
    log: Mutex<Log>,
    wake: Condvar,
    subscription: AtomicU64,
    ack: AtomicU64,
}

impl Run {
    fn append(&self, kind: &str, data: &[u8], code: Option<u32>) {
        let mut log = self.log.lock().unwrap_or_else(|e| e.into_inner());
        log.seq += 1;
        let mut info = self.info.lock().unwrap();
        if kind == "exit" {
            info.state = "exited".into();
            info.exit_code = code;
        }
        let event = RunEvent {
            run_id: info.id.clone(),
            seq: log.seq,
            kind: kind.into(),
            data: STANDARD.encode(data),
            exit_code: code,
        };
        log.bytes += event.data.len();
        log.items.push_back(event);
        while log.bytes > BUFFER_BYTES && log.items.len() > 1 {
            if let Some(old) = log.items.pop_front() {
                log.bytes -= old.data.len();
            }
        }
        self.wake.notify_all();
    }

    pub fn write(&self, bytes: &[u8]) -> Result<(), String> {
        if bytes.len() > 256 * 1024 {
            return Err("单次终端输入超过 256 KiB".into());
        }
        if self.info.lock().map_err(err)?.state != "running" {
            return Err("进程已退出".into());
        }
        self.writer
            .lock()
            .map_err(err)?
            .write_all(bytes)
            .map_err(err)
    }

    pub fn resize(&self, cols: u16, rows: u16) -> Result<(), String> {
        if cols == 0 || rows == 0 || cols > 1000 || rows > 1000 {
            return Err("终端尺寸无效".into());
        }
        self.master
            .lock()
            .map_err(err)?
            .resize(PtySize {
                cols,
                rows,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(err)
    }

    pub fn stop(&self) -> Result<(), String> {
        // Kill is a requested action. The wait thread alone observes exit.
        self.killer.lock().map_err(err)?.kill().map_err(err)
    }

    pub fn acknowledge(&self, seq: u64) {
        self.ack.fetch_max(seq, Ordering::SeqCst);
        self.wake.notify_all();
    }

    pub fn unsubscribe(&self) {
        self.subscription.fetch_add(1, Ordering::SeqCst);
        self.wake.notify_all();
    }

    pub fn subscribe(self: &Arc<Self>, after: u64, channel: Channel<RunEvent>) {
        let token = self.subscription.fetch_add(1, Ordering::SeqCst) + 1;
        self.ack.store(after, Ordering::SeqCst);
        let run = Arc::clone(self);
        std::thread::spawn(move || {
            let mut cursor = after;
            loop {
                let log = run.log.lock().unwrap_or_else(|e| e.into_inner());
                if run.subscription.load(Ordering::SeqCst) != token {
                    break;
                }
                if run.ack.load(Ordering::SeqCst) < cursor {
                    let _ = run.wake.wait_timeout(log, Duration::from_millis(500));
                    continue;
                }
                let first = log.items.front().map(|event| event.seq).unwrap_or(1);
                let next = if cursor.saturating_add(1) < first {
                    Some(RunEvent {
                        run_id: run.info.lock().unwrap().id.clone(),
                        seq: first - 1,
                        kind: "gap".into(),
                        data: String::new(),
                        exit_code: None,
                    })
                } else {
                    log.items.iter().find(|event| event.seq > cursor).cloned()
                };
                if let Some(event) = next {
                    cursor = event.seq;
                    drop(log);
                    if channel.send(event).is_err() {
                        break;
                    }
                } else {
                    if run.info.lock().unwrap().state == "exited" {
                        break;
                    }
                    let _ = run.wake.wait_timeout(log, Duration::from_millis(500));
                }
            }
        });
    }
}

fn err(error: impl std::fmt::Display) -> String {
    error.to_string()
}

fn find_command(name: &str) -> Option<PathBuf> {
    let path = Path::new(name);
    if path.is_absolute() {
        return is_executable(path).then(|| path.to_owned());
    }
    #[cfg(target_os = "macos")]
    let search_path = crate::macos::desktop_path();
    #[cfg(not(target_os = "macos"))]
    let search_path = std::env::var_os("PATH")?;
    std::env::split_paths(&search_path).find_map(|directory| {
        let path = directory.join(name);
        if is_executable(&path) {
            return Some(path);
        }
        if cfg!(windows) {
            let exe = path.with_extension("exe");
            if exe.is_file() {
                return Some(exe);
            }
        }
        None
    })
}

fn is_executable(path: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        path.metadata()
            .is_ok_and(|metadata| metadata.is_file() && metadata.permissions().mode() & 0o111 != 0)
    }
    #[cfg(not(unix))]
    path.is_file()
}

fn agent_command(agent: &str) -> Option<PathBuf> {
    match agent {
        "shell" => {
            let shell = if cfg!(windows) {
                std::env::var("COMSPEC").unwrap_or("cmd.exe".into())
            } else {
                let default = if cfg!(target_os = "macos") {
                    "/bin/zsh"
                } else {
                    "/bin/sh"
                };
                std::env::var("SHELL").unwrap_or_else(|_| default.into())
            };
            find_command(&shell)
        }
        "codex" | "claude" | "pi" | "hermes" => find_command(agent),
        _ => None,
    }
}

pub fn agents() -> Vec<Agent> {
    [
        ("codex", "Codex"),
        ("claude", "Claude Code"),
        ("pi", "Pi"),
        ("hermes", "Hermes"),
        ("shell", "系统终端"),
    ]
    .into_iter()
    .map(|(id, label)| Agent {
        id: id.into(),
        label: label.into(),
        installed: agent_command(id).is_some(),
        resume: false,
        structured_chat: false,
    })
    .collect()
}

#[derive(Default)]
pub struct Runtime {
    runs: Mutex<HashMap<String, Arc<Run>>>,
}

impl Runtime {
    pub fn get(&self, id: &str) -> Result<Arc<Run>, String> {
        self.runs
            .lock()
            .map_err(err)?
            .get(id)
            .cloned()
            .ok_or("终端运行不存在".into())
    }

    pub fn list(&self) -> Vec<RunInfo> {
        self.runs
            .lock()
            .unwrap()
            .values()
            .map(|run| run.info.lock().unwrap().clone())
            .collect()
    }

    pub fn spawn(
        &self,
        agent: &str,
        cwd: &str,
        session_id: Option<String>,
    ) -> Result<RunInfo, String> {
        let program = agent_command(agent).ok_or("Agent 命令未安装或不在 PATH 中")?;
        let mut command = CommandBuilder::new(program);
        #[cfg(target_os = "macos")]
        command.env("PATH", crate::macos::desktop_path());
        command.env("TERM", "xterm-256color");
        command.env("COLORTERM", "truecolor");
        command.cwd(cwd);
        self.spawn_command(command, agent, cwd, session_id)
    }

    fn spawn_command(
        &self,
        command: CommandBuilder,
        agent: &str,
        cwd: &str,
        session_id: Option<String>,
    ) -> Result<RunInfo, String> {
        if !Path::new(cwd).is_dir() {
            return Err("工作目录不存在".into());
        }
        let mut runs = self.runs.lock().map_err(err)?;
        if runs.len() >= 8 {
            return Err("最多保留 8 个终端，请先关闭已退出的终端".into());
        }
        let pair = native_pty_system()
            .openpty(PtySize {
                rows: 30,
                cols: 100,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(err)?;
        let mut reader = pair.master.try_clone_reader().map_err(err)?;
        let writer = pair.master.take_writer().map_err(err)?;
        let mut child = pair.slave.spawn_command(command).map_err(err)?;
        drop(pair.slave);
        let info = RunInfo {
            id: Uuid::new_v4().to_string(),
            session_id,
            agent: agent.into(),
            cwd: cwd.into(),
            pid: child.process_id(),
            state: "running".into(),
            exit_code: None,
        };
        let killer = child.clone_killer();
        let run = Arc::new(Run {
            info: Mutex::new(info.clone()),
            master: Mutex::new(pair.master),
            writer: Mutex::new(writer),
            killer: Mutex::new(killer),
            log: Mutex::new(Log {
                items: VecDeque::new(),
                bytes: 0,
                seq: 0,
            }),
            wake: Condvar::new(),
            subscription: AtomicU64::new(0),
            ack: AtomicU64::new(0),
        });
        runs.insert(info.id.clone(), Arc::clone(&run));
        let output = Arc::clone(&run);
        let reader_thread = std::thread::spawn(move || {
            let mut buffer = [0u8; 16384];
            loop {
                match reader.read(&mut buffer) {
                    Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
                    Ok(0) | Err(_) => break,
                    Ok(size) => output.append("output", &buffer[..size], None),
                }
            }
        });
        std::thread::spawn(move || {
            let status = child.wait();
            // Drain the PTY before publishing exit, so the final output is retained.
            let _ = reader_thread.join();
            let code = status.map(|status| status.exit_code()).ok();
            run.append("exit", &[], code);
        });
        Ok(info)
    }

    pub fn remove(&self, id: &str) -> Result<(), String> {
        let run = self.get(id)?;
        if run.info.lock().map_err(err)?.state == "running" {
            return Err("请先结束进程，再关闭终端".into());
        }
        run.unsubscribe();
        self.runs.lock().map_err(err)?.remove(id);
        Ok(())
    }

    pub fn shutdown(&self) {
        if let Ok(runs) = self.runs.lock() {
            for run in runs.values() {
                run.unsubscribe();
                if run.info.lock().unwrap().state == "running" {
                    let _ = run.stop();
                }
            }
        }
    }
}

impl Drop for Runtime {
    fn drop(&mut self) {
        self.shutdown();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[cfg(unix)]
    #[test]
    fn native_pty_keeps_unicode_and_observes_exit() {
        let runtime = Runtime::default();
        let mut command = CommandBuilder::new("/bin/sh");
        command.args(["-c", "printf '\\033[32m中文 PTY\\033[0m\\n'; exit 7"]);
        let info = runtime
            .spawn_command(command, "test", "/tmp", None)
            .unwrap();
        let run = runtime.get(&info.id).unwrap();
        run.resize(120, 32).unwrap();
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        while run.info.lock().unwrap().state != "exited" && std::time::Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(run.info.lock().unwrap().exit_code, Some(7));
        let log = run.log.lock().unwrap();
        let bytes: Vec<u8> = log
            .items
            .iter()
            .filter(|item| item.kind == "output")
            .flat_map(|item| STANDARD.decode(&item.data).unwrap())
            .collect();
        assert!(String::from_utf8(bytes)
            .unwrap()
            .contains("\u{1b}[32m中文 PTY"));
        assert_eq!(log.items.back().unwrap().kind, "exit");
    }

    #[cfg(unix)]
    #[test]
    fn channel_waits_for_ack_and_replays_after_sequence() {
        let runtime = Runtime::default();
        let mut command = CommandBuilder::new("/bin/sh");
        command.args(["-c", "printf 'channel test'; exit 0"]);
        let info = runtime
            .spawn_command(command, "test", "/tmp", None)
            .unwrap();
        let run = runtime.get(&info.id).unwrap();
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        while run.info.lock().unwrap().state != "exited" && std::time::Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        let (sender, receiver) = std::sync::mpsc::channel();
        let channel = Channel::new(move |body| {
            if let tauri::ipc::InvokeResponseBody::Json(text) = body {
                sender
                    .send(serde_json::from_str::<serde_json::Value>(&text).unwrap())
                    .unwrap();
            }
            Ok(())
        });
        run.subscribe(0, channel);
        let first = receiver.recv_timeout(Duration::from_secs(2)).unwrap();
        let first_seq = first["seq"].as_u64().unwrap();
        assert_eq!(first["kind"], "output");
        assert!(receiver.recv_timeout(Duration::from_millis(60)).is_err());
        run.acknowledge(first_seq);
        let mut event = receiver.recv_timeout(Duration::from_secs(2)).unwrap();
        while event["kind"] != "exit" {
            run.acknowledge(event["seq"].as_u64().unwrap());
            event = receiver.recv_timeout(Duration::from_secs(2)).unwrap();
        }
        let exit_seq = event["seq"].as_u64().unwrap();
        run.unsubscribe();
        let (sender, receiver) = std::sync::mpsc::channel();
        run.subscribe(
            exit_seq - 1,
            Channel::new(move |body| {
                if let tauri::ipc::InvokeResponseBody::Json(text) = body {
                    sender.send(text).unwrap();
                }
                Ok(())
            }),
        );
        let replay: serde_json::Value =
            serde_json::from_str(&receiver.recv_timeout(Duration::from_secs(2)).unwrap()).unwrap();
        assert_eq!(replay["seq"], exit_seq);
        assert_eq!(replay["kind"], "exit");
        run.unsubscribe();
    }
}
