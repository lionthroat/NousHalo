# Builds dist\NousHalo\NousHalo.exe, and installer-output\NousHalo-Setup.exe if
# Inno Setup 6 is installed. Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

& $py -m pip install --quiet -r (Join-Path $root "requirements.txt")
& $py (Join-Path $root "make_icon.py")

# Version comes from the VERSION file
$version = (Get-Content (Join-Path $root "VERSION") -Raw).Trim()
$parts = @($version.Split(".") + @("0", "0", "0"))[0..3]
New-Item -ItemType Directory -Force (Join-Path $root "build") | Out-Null
$versionFile = Join-Path $root "build\version_info.txt"
(Get-Content (Join-Path $root "version_info.txt") -Raw).
    Replace("{VERSION_TUPLE}", ($parts -join ", ")).Replace("{VERSION}", $version) |
    Set-Content $versionFile -Encoding utf8

& $py -m PyInstaller --noconfirm --clean --windowed --name NousHalo `
    --icon (Join-Path $root "icon.ico") `
    --version-file $versionFile `
    --add-data "$(Join-Path $root 'icon.ico');." `
    --add-data "$(Join-Path $root 'VERSION');." `
    --add-data "$(Join-Path $root 'assets');assets" `
    --distpath (Join-Path $root "dist") --workpath (Join-Path $root "build") `
    --specpath (Join-Path $root "build") `
    --exclude-module PySide6.QtNetwork --exclude-module PySide6.QtQml `
    --exclude-module PySide6.QtQuick --exclude-module PySide6.QtPdf `
    (Join-Path $root "noushalo.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# Qt drags in pieces this app never uses (QML, software OpenGL, Qt's own PDF
# module, TLS, the on-screen keyboard, translations). Drop them.
$qt = Join-Path $root "dist\NousHalo\_internal\PySide6"
$drop = @("opengl32sw.dll", "Qt6Quick*.dll", "Qt6Qml*.dll", "Qt6Pdf*.dll", "Qt6Network.dll",
          "QtNetwork.pyd", "Qt6VirtualKeyboard*.dll", "Qt6OpenGL*.dll", "translations",
          "plugins\tls", "plugins\platforminputcontexts", "plugins\networkinformation",
          "plugins\imageformats\qpdf.dll", "plugins\imageformats\qsvg.dll",
          "plugins\iconengines", "Qt6Svg.dll")
foreach ($d in $drop) { Remove-Item (Join-Path $qt $d) -Recurse -Force -ErrorAction SilentlyContinue }
$mb = (Get-ChildItem (Join-Path $root "dist\NousHalo") -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("Built: {0} (version {1}, {2:N0} MB)" -f (Join-Path $root 'dist\NousHalo\NousHalo.exe'), $version, $mb)

# The installer, if Inno Setup 6 is around
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    & $iscc /Q (Join-Path $root "installer.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    Write-Host "Built: $(Join-Path $root 'installer-output\NousHalo-Setup.exe')"
} else {
    Write-Host "(Inno Setup 6 not found, so no installer; GitHub builds it on version tags)"
}
