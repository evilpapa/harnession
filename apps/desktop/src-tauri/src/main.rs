#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    #[cfg(target_os = "linux")]
    configure_linux_graphics();

    harnession_desktop::run();
}

#[cfg(target_os = "linux")]
fn configure_linux_graphics() {
    use std::{env, fs};

    if env::var_os("GDK_GL").is_some() {
        return;
    }

    let wayland = match env::var("GDK_BACKEND").as_deref() {
        Ok(backends) if !backends.is_empty() && !backends.starts_with('*') => {
            backends.split(',').next() == Some("wayland")
        }
        _ => {
            env::var("XDG_SESSION_TYPE").as_deref() == Ok("wayland")
                || env::var_os("WAYLAND_DISPLAY").is_some_and(|display| !display.is_empty())
        }
    };
    if !wayland {
        return;
    }

    let Ok(version) = fs::read_to_string("/sys/module/nvidia/version") else {
        return;
    };
    if version.trim().split('.').next() == Some("580") {
        // GTK 3's Wayland EGL context can fail on NVIDIA's 580 driver.
        // Select software rendering before GTK or WebKit starts.
        env::set_var("GDK_GL", "disable");
        if env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
            // Skip WebKit's hardware-rendering probe when GTK GL is disabled.
            env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
        }
    }
}
