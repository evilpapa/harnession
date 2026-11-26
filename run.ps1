$ErrorActionPreference = 'Stop'

$releaseDir = Join-Path $PSScriptRoot 'apps\desktop\src-tauri\target\release'
$app = Join-Path $releaseDir 'harnession-desktop.exe'
$gateway = Join-Path $releaseDir 'harnession-gateway.exe'

foreach ($path in @($app, $gateway)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "File not found: $path. Run 'npm run desktop:build' in apps\desktop first."
    }
}

# On some Windows hosts WebView2 is installed but automatic discovery fails.
# Honor an explicit caller override; otherwise prefer the registered system runtime.
$setWebView = $false
if (-not $env:WEBVIEW2_BROWSER_EXECUTABLE_FOLDER) {
    $client = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    $installs = @(
        @{ Key = "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$client"
           Root = Join-Path ${env:ProgramFiles(x86)} 'Microsoft\EdgeWebView\Application' }
        @{ Key = "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$client"
           Root = Join-Path $env:LOCALAPPDATA 'Microsoft\EdgeWebView\Application' }
    )
    foreach ($install in $installs) {
        $version = (Get-ItemProperty -LiteralPath $install.Key -Name pv -ErrorAction SilentlyContinue).pv
        if ($version -notmatch '^\d+\.\d+\.\d+\.\d+$') { continue }
        $folder = Join-Path $install.Root $version
        if (Test-Path -LiteralPath (Join-Path $folder 'msedgewebview2.exe') -PathType Leaf) {
            $env:WEBVIEW2_BROWSER_EXECUTABLE_FOLDER = $folder
            $setWebView = $true
            break
        }
    }
}

try {
    Start-Process -FilePath $app -WorkingDirectory $releaseDir
} finally {
    if ($setWebView) { Remove-Item Env:WEBVIEW2_BROWSER_EXECUTABLE_FOLDER }
}
