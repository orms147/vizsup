# Launch the vizsup desktop app using the PROJECT VENV, which has everything
# (PySide6 + the backend `app` package + faster-whisper). Avoids the "global
# python is missing deps" trap. Run from anywhere:  .\run-desktop.ps1
Push-Location $PSScriptRoot   # repo root, so `-m desktop.main` and ./storage resolve
# Reload PATH from the registry so freshly-installed tools (e.g. winget ffmpeg) are
# visible even if THIS shell was opened before the install (common in VS Code).
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path", "User")
try {
    & "$PSScriptRoot\backend\.venv\Scripts\python.exe" -m desktop.main @args
}
finally {
    Pop-Location
}
