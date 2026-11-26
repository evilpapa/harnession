use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

pub struct Gateway {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
    next_id: u64,
}

impl Gateway {
    pub fn start() -> Result<Self, String> {
        Self::start_with_database(None)
    }

    fn start_with_database(database: Option<&std::path::Path>) -> Result<Self, String> {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..");
        let mut command;
        if cfg!(debug_assertions) {
            command = Command::new(std::env::var("HARNESSION_PYTHON").unwrap_or_else(|_| {
                if cfg!(windows) {
                    "python".into()
                } else {
                    "python3".into()
                }
            }));
            command.args(["-m", "harnession.gateway"]).current_dir(root);
        } else {
            let name = if cfg!(windows) {
                "harnession-gateway.exe"
            } else {
                "harnession-gateway"
            };
            let sidecar = std::env::current_exe()
                .map_err(|e| e.to_string())?
                .with_file_name(name);
            command = Command::new(sidecar);
        }
        #[cfg(target_os = "macos")]
        command.env("PATH", crate::macos::desktop_path());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            // The GUI host needs pipes to the console sidecar, not a console window.
            const CREATE_NO_WINDOW: u32 = 0x08000000;
            command.creation_flags(CREATE_NO_WINDOW);
        }
        command.arg("--allow-delete");
        if let Some(path) = database {
            command.arg("--db").arg(path);
        }
        let mut child = command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|e| format!("无法启动索引服务：{e}"))?;
        Ok(Self {
            input: child.stdin.take().ok_or("索引服务 stdin 不可用")?,
            output: BufReader::new(child.stdout.take().ok_or("索引服务 stdout 不可用")?),
            child,
            next_id: 0,
        })
    }

    pub fn call(&mut self, method: &str, params: Value) -> Result<Value, String> {
        self.next_id += 1;
        let request =
            json!({"id":self.next_id,"protocol_version":1,"method":method,"params":params});
        writeln!(self.input, "{request}")
            .and_then(|_| self.input.flush())
            .map_err(|e| e.to_string())?;
        let mut line = String::new();
        if self
            .output
            .read_line(&mut line)
            .map_err(|e| e.to_string())?
            == 0
        {
            return Err("本地索引服务已退出，请重新打开应用".into());
        }
        let response: Value =
            serde_json::from_str(&line).map_err(|e| format!("索引协议错误：{e}"))?;
        if response["id"] != self.next_id {
            return Err("索引响应序号不匹配".into());
        }
        if let Some(error) = response.get("error") {
            return Err(error["message"]
                .as_str()
                .unwrap_or("索引请求失败")
                .to_owned());
        }
        if std::env::var_os("HARNESSION_TRACE").is_some() {
            // Method names only: never log paths, queries, messages or credentials.
            eprintln!("[harnession] completed index method: {method}");
        }
        Ok(response["result"].clone())
    }
}

impl Drop for Gateway {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn jsonl_bridge_uses_real_python_and_recovers_from_request_errors() {
        let directory = tempfile::tempdir().unwrap();
        let mut gateway =
            Gateway::start_with_database(Some(&directory.path().join("test.sqlite3"))).unwrap();
        let state = gateway.call("bootstrap", json!({})).unwrap();
        assert_eq!(state["protocol_version"], 1);
        assert_eq!(state["total"], 0);
        assert!(gateway.call("unsupported", json!({})).is_err());
        let sessions = gateway.call("list_sessions", json!({"limit":10})).unwrap();
        assert_eq!(sessions["items"].as_array().unwrap().len(), 0);
    }
}
