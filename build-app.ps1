# Build vizsup.exe (the double-click app) and put shortcuts on the Desktop and in the Start menu.
# Uses the C# compiler that ships with Windows (.NET Framework 4) — nothing is downloaded.
# The exe starts the desktop app with this project's venv (backend\.venv) — see
# desktop\launcher\vizsup.cs. Run from anywhere:  .\build-app.ps1   (or: make app)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe" }
if (-not (Test-Path $csc)) { throw "Không thấy csc.exe (.NET Framework 4) trong Windows." }
$ico = Join-Path $root "desktop\assets\vizsup.ico"
if (-not (Test-Path $ico)) {
    & (Join-Path $root "backend\.venv\Scripts\python.exe") (Join-Path $root "desktop\launcher\make_icon.py")
}
$exe = Join-Path $root "vizsup.exe"
& $csc /nologo /target:winexe /codepage:65001 /optimize+ "/out:$exe" "/win32icon:$ico" `
    /reference:System.Windows.Forms.dll (Join-Path $root "desktop\launcher\vizsup.cs")
if ($LASTEXITCODE -ne 0) { throw "Biên dịch vizsup.exe lỗi." }
Write-Host "Đã tạo $exe"

$shell = New-Object -ComObject WScript.Shell
$places = @(
    [Environment]::GetFolderPath("Desktop"),
    (Join-Path ([Environment]::GetFolderPath("Programs")) "")
)
foreach ($dir in $places) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "vizsup.lnk"))
    $lnk.TargetPath = $exe
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = "$ico,0"
    $lnk.Description = "vizsup — vietsub + lồng tiếng"
    $lnk.Save()
    Write-Host "Lối tắt: $(Join-Path $dir 'vizsup.lnk')"
}
