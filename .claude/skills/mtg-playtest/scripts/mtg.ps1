# Run the CLI even when Python is not on PATH (Codex desktop on Windows).
$pythonCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue
if ($pythonCommand) {
    $mtgPython = $pythonCommand.Source
} else {
    $mtgPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
}
if (-not (Test-Path -LiteralPath $mtgPython -PathType Leaf)) {
    throw 'Python was not found. Locate an installed Python runtime and invoke mtg.py with its absolute path.'
}
& $mtgPython (Join-Path $PSScriptRoot 'mtg.py') @args
exit $LASTEXITCODE
