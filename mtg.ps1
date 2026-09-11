# Short entry point; use from the project root to keep cache/deck paths consistent.
$mtgEntry = Join-Path $PSScriptRoot '.claude/skills/mtg-playtest/scripts/mtg.ps1'
if ($MyInvocation.ExpectingInput) {
    $input | & $mtgEntry @args
} else {
    & $mtgEntry @args
}
exit $LASTEXITCODE
