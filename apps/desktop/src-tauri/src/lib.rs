mod gateway;
#[cfg(target_os = "macos")]
mod macos;
mod runtime;

use gateway::Gateway;
use runtime::{Agent, RunEvent, RunInfo, Runtime};
use serde_json::Value;
use std::sync::Mutex;
use tauri::{ipc::Channel, Manager, State};

struct IndexService(Mutex<Gateway>);

#[tauri::command]
async fn index_request(
    app: tauri::AppHandle,
    method: String,
    params: Value,
) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        app.state::<IndexService>()
            .0
            .lock()
            .map_err(|e| e.to_string())?
            .call(&method, params)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
fn list_agents() -> Vec<Agent> {
    runtime::agents()
}

#[tauri::command]
fn list_runs(runtime: State<'_, Runtime>) -> Vec<RunInfo> {
    runtime.list()
}

#[tauri::command]
async fn spawn_run(
    app: tauri::AppHandle,
    agent: String,
    session_id: Option<String>,
    cwd: Option<String>,
) -> Result<RunInfo, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let directory = if let Some(ref id) = session_id {
            let service = app.state::<IndexService>();
            let session = service
                .0
                .lock()
                .map_err(|e| e.to_string())?
                .call("get_session", serde_json::json!({"session_id":id}))?;
            session["cwd"]
                .as_str()
                .ok_or("此会话缺少工作目录")?
                .to_owned()
        } else {
            cwd.ok_or("请选择工作目录")?
        };
        app.state::<Runtime>().spawn(&agent, &directory, session_id)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
fn subscribe_run(
    runtime: State<'_, Runtime>,
    run_id: String,
    after_seq: u64,
    channel: Channel<RunEvent>,
) -> Result<(), String> {
    runtime.get(&run_id)?.subscribe(after_seq, channel);
    Ok(())
}

#[tauri::command]
fn acknowledge_run(runtime: State<'_, Runtime>, run_id: String, seq: u64) -> Result<(), String> {
    runtime.get(&run_id)?.acknowledge(seq);
    Ok(())
}

#[tauri::command]
fn unsubscribe_run(runtime: State<'_, Runtime>, run_id: String) -> Result<(), String> {
    runtime.get(&run_id)?.unsubscribe();
    Ok(())
}

#[tauri::command]
async fn write_pty(app: tauri::AppHandle, run_id: String, bytes: Vec<u8>) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || app.state::<Runtime>().get(&run_id)?.write(&bytes))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
fn resize_pty(
    runtime: State<'_, Runtime>,
    run_id: String,
    cols: u16,
    rows: u16,
) -> Result<(), String> {
    runtime.get(&run_id)?.resize(cols, rows)
}

#[tauri::command]
fn stop_run(runtime: State<'_, Runtime>, run_id: String) -> Result<(), String> {
    runtime.get(&run_id)?.stop()
}

#[tauri::command]
fn close_run(runtime: State<'_, Runtime>, run_id: String) -> Result<(), String> {
    runtime.remove(&run_id)
}

pub fn run() {
    let application = tauri::Builder::default()
        .setup(|app| {
            let gateway = Gateway::start().map_err(std::io::Error::other)?;
            app.manage(IndexService(Mutex::new(gateway)));
            app.manage(Runtime::default());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            index_request,
            list_agents,
            list_runs,
            spawn_run,
            subscribe_run,
            acknowledge_run,
            unsubscribe_run,
            write_pty,
            resize_pty,
            stop_run,
            close_run
        ])
        .build(tauri::generate_context!())
        .expect("Harnession desktop runtime failed");
    application.run(|app, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            app.state::<Runtime>().shutdown();
        }
    });
}
