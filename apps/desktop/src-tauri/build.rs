fn main() {
    println!(
        "cargo:rustc-env=HARNESSION_TARGET={}",
        std::env::var("TARGET").unwrap()
    );
    tauri_build::build()
}
