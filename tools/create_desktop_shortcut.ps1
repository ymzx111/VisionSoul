# 仅创建桌面入口；后端继续由用户在终端启动。
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$desktopDirectory = [Environment]::GetFolderPath('DesktopDirectory')
$applicationUrl = 'http://127.0.0.1:8000/app/'
$browserCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
    (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
)
$browserPath = $browserCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $browserPath) { throw '未找到 Edge 或 Chrome，请安装其中一个浏览器后重试。' }
if (-not (Test-Path -LiteralPath $desktopDirectory -PathType Container)) { throw '未找到桌面目录。' }

$shortcutPath = Join-Path $desktopDirectory 'VisionSoul.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$expectedArguments = "--app=$applicationUrl"
if ((Test-Path -LiteralPath $shortcutPath) -and
    ($shortcut.TargetPath -ne $browserPath -or $shortcut.Arguments -ne $expectedArguments)) {
    throw '桌面已有其他同名入口，未覆盖。请先检查原快捷方式。'
}
$shortcut.TargetPath = $browserPath
$shortcut.Arguments = $expectedArguments
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = 'VisionSoul 本机工作台；使用前请先在终端启动后端服务。'
$shortcut.IconLocation = "$browserPath,0"
$shortcut.WindowStyle = 1
$shortcut.Save()

$verified = $shell.CreateShortcut($shortcutPath)
if ($verified.TargetPath -ne $browserPath -or $verified.Arguments -ne $expectedArguments) {
    throw '快捷方式回读校验失败，请检查桌面文件。'
}
Write-Output "已创建并核对：$shortcutPath"
Write-Output "工作台地址：$applicationUrl"
