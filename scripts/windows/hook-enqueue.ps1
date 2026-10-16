[CmdletBinding()]
param([string] $Event, [string] $Type, [string] $Project)

# Run inside the Codex sandbox: .NET file I/O only, never start MSYS.
# Validation failures remain fail-open, with a visible diagnostic.
$ErrorActionPreference = 'Stop'
$temporary = $null
try {
    if ($Event -cnotin @('SessionStart', 'SessionEnd') -or $Type -cne 'codex') {
        throw 'Only codex SessionStart/SessionEnd requests are supported.'
    }
    if ($Project -notmatch '^[A-Za-z]:[\\/]' -or $Project -match '[\x00-\x1f]') {
        throw 'Project must be an absolute local Windows directory.'
    }
    $Project = [IO.Path]::GetFullPath($Project)
    if (-not [IO.Directory]::Exists($Project)) { throw 'Project does not exist.' }
    [Console]::InputEncoding = New-Object Text.UTF8Encoding $false
    $inputText = [Console]::In.ReadToEnd()
    if ([Text.Encoding]::UTF8.GetByteCount($inputText) -gt 1MB) { throw 'Hook input exceeds 1 MiB.' }
    $request = [ordered]@{
        version = 1
        id = [Guid]::NewGuid().ToString('N')
        # Numeric time avoids PowerShell 7's automatic ISO-string -> DateTime conversion.
        created = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
        event = $Event
        type = $Type
        project = $Project
        stdin = $inputText
        thread = [string]$env:CODEX_THREAD_ID
        seat = [string]$env:AGMSG_CODEX_SEAT_KEY
    }
    $queue = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\run\hook-queue'))
    [void][IO.Directory]::CreateDirectory($queue)
    $destination = Join-Path $queue ($request.id + '.req')
    $temporary = $destination + '.tmp'
    [IO.File]::WriteAllText($temporary, ($request | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding $false))
    [IO.File]::Move($temporary, $destination)
} catch {
    $message = 'agmsg hook enqueue failed: ' + $_.Exception.Message
    [Console]::Error.WriteLine($message)
    try {
        [IO.File]::AppendAllText((Join-Path $env:TEMP 'agmsg-hook-enqueue.err'),
            ([DateTime]::UtcNow.ToString('o') + ' ' + $message + [Environment]::NewLine))
    } catch { }
} finally {
    if ($temporary -and [IO.File]::Exists($temporary)) {
        try { [IO.File]::Delete($temporary) } catch { }
    }
}
exit 0
