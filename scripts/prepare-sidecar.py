"""Build a self-contained Python gateway for the current Rust target."""
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent.parent
host = next(line.split(': ', 1)[1] for line in subprocess.check_output(
    ['rustc', '-vV'], text=True).splitlines() if line.startswith('host: '))
directory = root / 'apps' / 'desktop' / 'src-tauri' / 'binaries'
directory.mkdir(parents=True, exist_ok=True)
subprocess.run([
    'uv', 'run', '--locked', '--with', 'pyinstaller==6.22.3', 'python', '-m', 'PyInstaller',
    '--onefile', '--noconfirm', '--name', 'harnession-gateway-' + host,
    '--paths', str(root), '--distpath', str(directory),
    '--workpath', str(root / 'build' / 'sidecar'),
    '--specpath', str(root / 'build'), str(root / 'scripts' / 'sidecar_entry.py'),
], cwd=root, check=True)
