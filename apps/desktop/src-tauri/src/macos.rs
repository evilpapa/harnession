use std::env;
use std::ffi::{OsStr, OsString};
use std::path::{Path, PathBuf};

pub fn desktop_path() -> OsString {
    let inherited = env::var_os("PATH").unwrap_or_default();
    let home = env::var_os("HOME").map(PathBuf::from);
    application_path(&inherited, home.as_deref())
}

fn application_path(inherited: &OsStr, home: Option<&Path>) -> OsString {
    // LaunchServices starts GUI apps with a system PATH. Keep explicit entries first.
    let mut paths: Vec<_> = env::split_paths(inherited)
        .filter(|path| path.is_absolute())
        .collect();
    for path in home.into_iter().map(|home| home.join(".local/bin")).chain(
        [
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        ]
        .into_iter()
        .map(PathBuf::from),
    ) {
        if path.is_absolute() && !paths.contains(&path) && env::join_paths([&path]).is_ok() {
            paths.push(path);
        }
    }
    env::join_paths(paths).expect("valid absolute PATH entries")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finder_path_keeps_explicit_precedence_and_adds_cli_install_locations() {
        let path = application_path(
            OsStr::new("/custom/bin:.:/usr/bin"),
            Some(Path::new("/Users/demo user")),
        );
        let paths: Vec<_> = env::split_paths(&path).collect();
        assert_eq!(
            paths[..5],
            [
                "/custom/bin",
                "/usr/bin",
                "/Users/demo user/.local/bin",
                "/opt/homebrew/bin",
                "/usr/local/bin"
            ]
            .map(PathBuf::from)
        );
        assert!(!paths.contains(&PathBuf::from(".")));
    }
}
