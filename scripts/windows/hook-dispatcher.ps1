[CmdletBinding()]
param(
    [ValidateSet('Run', 'Start', 'Stop', 'Status')] [string] $Action = 'Run',
    [string[]] $Project = @(),
    [string] $Bash = '',
    [switch] $Once,
    [ValidateRange(1, 3600)] [int] $PollSeconds = 1,
    [ValidateRange(1, 3600)] [int] $HookTimeoutSeconds = 60
)

# Run from a normal terminal, never a Codex hook. The project allowlist is
# snapshotted from these command-line arguments, never a sandbox-writable file.
$ErrorActionPreference = 'Stop'
$SkillDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$RunDir = Join-Path $SkillDir 'run'
$Queue = Join-Path $RunDir 'hook-queue'
$LockFile = Join-Path $RunDir 'hook-dispatcher.lock'
$StopFile = Join-Path $RunDir 'hook-dispatcher.stop'
$LogFile = Join-Path $RunDir 'hook-dispatcher.log'
$Utf8 = New-Object Text.UTF8Encoding $false
[Console]::InputEncoding = $Utf8

function Assert-LocalPath([string] $Path) {
    if ($Path -notmatch '^[A-Za-z]:[\\/]' -or $Path -match '[\x00-\x1f]') {
        throw 'Only absolute local Windows paths are supported.'
    }
    $full = [IO.Path]::GetFullPath($Path)
    # Reject junctions/symlinks at every existing component, including parents.
    for ($part = $full; $part; $part = [IO.Path]::GetDirectoryName($part)) {
        if (Test-Path -LiteralPath $part) {
            if ((Get-Item -Force -LiteralPath $part).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Reparse points are not supported: $part"
            }
        }
    }
    return $full
}

function Write-Log([string] $Message) {
    # Log metadata and hook stderr, without copying request stdin or hook stdout.
    if ([IO.File]::Exists($LogFile) -and (Get-Item -LiteralPath $LogFile).Length -gt 1MB) {
        [IO.File]::WriteAllText($LogFile, '', $Utf8)
    }
    [IO.File]::AppendAllText($LogFile, ([DateTime]::UtcNow.ToString('o') + ' ' +
        ($Message -replace '[\r\n]', ' ') + [Environment]::NewLine), $Utf8)
}

function Quote-PS([string] $Value) { return "'" + $Value.Replace("'", "''") + "'" }
function Msys-Path([string] $Value) {
    return '/' + $Value.Substring(0, 1).ToLowerInvariant() + $Value.Substring(2).Replace('\', '/')
}

function Write-QueueStatus {
    Write-Output "Queue/results: $Queue; log: $LogFile"
    foreach ($state in @('req', 'processing', 'done', 'failed', 'uncertain')) {
        $count = @(Get-ChildItem -LiteralPath $Queue -Filter "*.$state" -File -ErrorAction SilentlyContinue).Count
        Write-Output "$($state): $count"
    }
}

function Stop-HookProcess([Diagnostics.Process] $Process) {
    if (-not $Process.HasExited) {
        # Kill only the child we just spawned, including its descendants. No
        # pidfile or queue data can choose the PID. PS 5.1 has no Kill(true).
        $saved = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            & "$env:SystemRoot\System32\taskkill.exe" /PID $Process.Id /T /F 2>&1 | Out-Null
        } finally { $ErrorActionPreference = $saved }
    }
    if (-not $Process.WaitForExit(5000)) { throw 'Timed-out hook process did not exit.' }
}

function Test-MonitorEnabled([string] $Directory, [string] $Event) {
    $hooksPath = Assert-LocalPath (Join-Path $Directory '.codex\hooks.json')
    if (-not [IO.File]::Exists($hooksPath)) { return $false }
    $settings = [IO.File]::ReadAllText($hooksPath) | ConvertFrom-Json
    $enqueue = Quote-PS (($SkillDir + '\scripts\windows\hook-enqueue.ps1').Replace('\', '/'))
    foreach ($entry in $settings.hooks.$Event) {
        foreach ($hook in $entry.hooks) {
            if ($hook.type -eq 'command' -and ([string]$hook.commandWindows).Contains($enqueue)) { return $true }
        }
    }
    return $false
}

function Invoke-Request([IO.FileInfo] $File) {
    $claimed = Join-Path $Queue ($File.BaseName + '.processing')
    $child = $null
    $started = $false
    $outcome = 'failed'
    try {
        [void](Assert-LocalPath $File.FullName)
        if ($File.Name -cnotmatch '^[a-f0-9]{32}\.req$') { throw 'Invalid request filename.' }
        # A terminal state is also the dedup receipt. Never replay one id.
        foreach ($state in @('done', 'failed', 'uncertain')) {
            if ([IO.File]::Exists((Join-Path $Queue ($File.BaseName + '.' + $state)))) {
                [IO.File]::Delete($File.FullName)
                Write-Log "request=$($File.BaseName) status=duplicate"
                return
            }
        }
        [IO.File]::Move($File.FullName, $claimed)
        if ((Get-Item -LiteralPath $claimed).Length -gt 2MB) { throw 'Request exceeds 2 MiB.' }
        $request = [IO.File]::ReadAllText($claimed) | ConvertFrom-Json
        $keys = @($request.PSObject.Properties.Name | Sort-Object)
        if (($keys -join ',') -cne 'created,event,id,project,seat,stdin,thread,type,version' -or
            ($request.version -isnot [int] -and $request.version -isnot [long]) -or
            $request.version -ne 1 -or $request.id -cne $File.BaseName -or
            $request.type -cne 'codex' -or $request.event -cnotin @('SessionStart', 'SessionEnd')) {
            throw 'Invalid request schema, id, event or agent type.'
        }
        if (($request.created -isnot [int] -and $request.created -isnot [long]) -or $request.created -le 0) {
            throw 'Invalid creation timestamp.'
        }
        foreach ($key in @('id', 'event', 'type', 'project', 'stdin', 'thread', 'seat')) {
            if ($request.$key -isnot [string]) { throw "Non-string field: $key" }
        }
        $directory = Assert-LocalPath $request.project
        if (-not $Allowed.ContainsKey($directory.TrimEnd('\'))) { throw 'Project is not allowed.' }
        if (-not (Test-MonitorEnabled $directory $request.event)) { throw 'Monitor hook is disabled or belongs to another installation.' }
        if ($request.seat -and ($request.seat.Length -gt 128 -or $request.seat -cnotmatch '^\d+(\.\d+)*$')) {
            throw 'Invalid Codex seat key.'
        }
        $payload = $request.stdin | ConvertFrom-Json
        if ($payload -isnot [pscustomobject]) { throw 'Hook stdin must be a JSON object.' }
        $thread = $request.thread
        if ($payload.session_id) {
            if ($payload.session_id -isnot [string]) { throw 'Invalid session id.' }
            if ($thread -and $thread -cne $payload.session_id) { throw 'Conflicting session ids.' }
            $thread = $payload.session_id
        }
        if (-not $thread -or $thread -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' -or $thread.Contains('..')) {
            throw 'Missing or unsafe session id; refusing asynchronous rollout guessing.'
        }
        # The only executable is a fixed installed script. Queue contents never
        # become a script file, bash -c string, or Windows command-line argument.
        $scriptPath = Assert-LocalPath (Join-Path $PSScriptRoot 'hook-replay.sh')
        $start = New-Object Diagnostics.ProcessStartInfo
        $start.FileName = $Bash
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $start.WorkingDirectory = $directory
        # Windows filenames cannot contain quotes; the installed script path
        # ends in .sh, so no trailing-backslash quoting ambiguity on PS 5.1.
        $start.Arguments = '--noprofile --norc "' + (Msys-Path $scriptPath) + '"'
        $start.RedirectStandardInput = $true
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        $start.StandardOutputEncoding = $Utf8
        $start.StandardErrorEncoding = $Utf8
        # The request has no generic env/command field. Only these bounded data
        # values cross the boundary; endpoints are resolved by existing seat logic.
        $start.EnvironmentVariables['CODEX_THREAD_ID'] = $thread
        $start.EnvironmentVariables['AGMSG_CODEX_SEAT_KEY'] = $request.seat
        $start.EnvironmentVariables['AGMSG_CODEX_BRIDGE_LAUNCHER'] = '1'
        $start.EnvironmentVariables['AGMSG_HOOK_EVENT'] = $request.event
        $start.EnvironmentVariables['AGMSG_HOOK_PROJECT'] = $directory
        $start.EnvironmentVariables.Remove('AGMSG_CODEX_BRIDGE_APP_SERVER')
        $child = New-Object Diagnostics.Process
        $child.StartInfo = $start
        $timer = [Diagnostics.Stopwatch]::StartNew()
        [void]$child.Start()
        $started = $true
        $stdout = $child.StandardOutput.ReadToEndAsync()
        $stderr = $child.StandardError.ReadToEndAsync()
        # .NET Framework (Windows PowerShell 5.1) has no StandardInputEncoding.
        # Write UTF-8 bytes directly, with a deadline even if a child never reads.
        $inputBytes = $Utf8.GetBytes($request.stdin)
        $write = $child.StandardInput.BaseStream.WriteAsync($inputBytes, 0, $inputBytes.Length)
        $remaining = [Math]::Max(1, $HookTimeoutSeconds * 1000 - [int]$timer.ElapsedMilliseconds)
        if (-not $write.Wait($remaining)) {
            $outcome = 'uncertain'
            Stop-HookProcess $child
            throw 'Hook stdin timed out; delivery outcome may be uncertain.'
        }
        $write.GetAwaiter().GetResult()
        $child.StandardInput.Close()
        $remaining = [Math]::Max(1, $HookTimeoutSeconds * 1000 - [int]$timer.ElapsedMilliseconds)
        if (-not $child.WaitForExit($remaining)) {
            # Delivery may already have happened. Never retry a timed-out request.
            $outcome = 'uncertain'
            Stop-HookProcess $child
            throw 'Hook timed out; delivery outcome may be uncertain.'
        }
        $remaining = [Math]::Max(1, $HookTimeoutSeconds * 1000 - [int]$timer.ElapsedMilliseconds)
        $stdoutReady = $stdout.Wait($remaining)
        $remaining = [Math]::Max(1, $HookTimeoutSeconds * 1000 - [int]$timer.ElapsedMilliseconds)
        if (-not $stdoutReady -or -not $stderr.Wait($remaining)) {
            $outcome = 'uncertain'
            throw 'Hook exited but a descendant retained its output handles.'
        }
        if ($child.ExitCode -ne 0) { throw "Hook exited $($child.ExitCode): $($stderr.Result)" }
        if ($stderr.Result) { Write-Log "request=$($File.BaseName) hook-stderr=$($stderr.Result)" }
        [IO.File]::Move($claimed, (Join-Path $Queue ($File.BaseName + '.done')))
        Write-Log "request=$($File.BaseName) event=$($request.event) status=ok hook-exit=0"
    } catch {
        Write-Log "request=$($File.BaseName) status=$outcome error=$($_.Exception.Message)"
        if ([IO.File]::Exists($claimed)) {
            [IO.File]::Move($claimed, (Join-Path $Queue ($File.BaseName + '.' + $outcome)))
        } elseif ([IO.File]::Exists($File.FullName)) {
            # Invalid filenames are quarantined without deriving an executable path.
            [IO.File]::Move($File.FullName, ($File.FullName + '.failed'))
        }
    } finally {
        if ($child) {
            try {
                if ($started -and -not $child.HasExited) { Stop-HookProcess $child }
            } finally { $child.Dispose() }
        }
    }
}

[void](Assert-LocalPath $RunDir)
[void][IO.Directory]::CreateDirectory($RunDir)
[void](Assert-LocalPath $LockFile)
[void](Assert-LocalPath $StopFile)
[void](Assert-LocalPath $LogFile)
$lock = $null
try {
    # FileShare.Read keeps the live lock readable but prevents replacement,
    # deletion, and another writer. OS closes it automatically after a crash.
    $lock = [IO.File]::Open($LockFile, 'OpenOrCreate', 'ReadWrite', 'Read')
} catch [IO.IOException] {
    if ($Action -eq 'Stop') {
        [IO.File]::WriteAllText($StopFile, 'stop', $Utf8)
        Write-Output 'Dispatcher stop requested; the current hook finishes first.'
        exit 0
    }
    if ($Action -eq 'Status' -or $Action -eq 'Start') {
        if ($Action -eq 'Start') {
            # Inspect the live owner's immutable lock metadata, never enlarge
            # its allowlist from sandbox-writable requests or another Start.
            $reader = $null
            try {
                $stream = [IO.File]::Open($LockFile, 'Open', 'Read', 'ReadWrite')
                $reader = New-Object IO.StreamReader($stream, $Utf8)
                $owner = $reader.ReadToEnd() | ConvertFrom-Json
                if ($owner.version -ne 1) { throw 'Unsupported dispatcher metadata.' }
                foreach ($directory in $Project) {
                    $full = (Assert-LocalPath $directory).TrimEnd('\')
                    if ($full -notin @($owner.projects)) { throw "Project is not allowed by the running dispatcher: $full" }
                }
            } catch {
                throw "Cannot confirm the running dispatcher's allowlist. Stop it and restart outside Codex with all required -Project values. $($_.Exception.Message)"
            } finally { if ($reader) { $reader.Dispose() } }
        }
        Write-Output 'Dispatcher running (exclusive installation lock held).'
        Write-QueueStatus
        exit 0
    }
    throw 'Another dispatcher holds this installation lock.'
}
try {
    if ($Action -eq 'Stop' -or $Action -eq 'Status') {
        Write-Output 'Dispatcher stopped.'
        Write-QueueStatus
        exit 0
    }
    $Allowed = @{}
    foreach ($directory in $Project) {
        $full = Assert-LocalPath $directory
        if (-not [IO.Directory]::Exists($full)) { throw "Project does not exist: $full" }
        $Allowed[$full.TrimEnd('\')] = $true
    }
    if ($Allowed.Count -eq 0) { throw 'Supply -Project with the exact allowed project directories.' }
    if (-not $Bash) { $Bash = $env:GIT_BASH }
    if (-not $Bash) { $Bash = $env:AGMSG_BASH }
    if (-not $Bash) { $Bash = 'C:\Program Files\Git\bin\bash.exe' }
    $Bash = Assert-LocalPath $Bash
    if (-not [IO.File]::Exists($Bash)) { throw "Git Bash not found: $Bash" }
    if ($Action -eq 'Start') {
        $expression = '& ' + (Quote-PS $PSCommandPath) + ' -Project @(' +
            (($Project | ForEach-Object { Quote-PS $_ }) -join ',') + ') -Bash ' + (Quote-PS $Bash) +
            ' -PollSeconds ' + $PollSeconds + ' -HookTimeoutSeconds ' + $HookTimeoutSeconds
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($expression))
        $engine = (Get-Process -Id $PID).Path
        # Release before the child takes the same lock; concurrent starts are
        # still serialized by the child's exclusive open.
        $lock.Dispose()
        $lock = $null
        $background = Start-Process -FilePath $engine -ArgumentList @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded) -WindowStyle Hidden -PassThru
        $ready = $false
        for ($attempt = 0; $attempt -lt 50; $attempt++) {
            if ($background.HasExited) { throw "Dispatcher exited during startup ($($background.ExitCode)); inspect $LogFile" }
            try {
                $probe = [IO.File]::Open($LockFile, 'Open', 'ReadWrite', 'Read')
                $probe.Dispose()
            } catch [IO.IOException] { $ready = $true; break }
            Start-Sleep -Milliseconds 100
        }
        if (-not $ready) { throw "Dispatcher startup not confirmed; inspect $LogFile" }
        Write-Output "Dispatcher started. Use -Action Status for queue counts. Log: $LogFile"
        exit 0
    }
    $metadata = $Utf8.GetBytes((@{ version = 1; pid = $PID; projects = @($Allowed.Keys) } | ConvertTo-Json -Compress))
    $lock.SetLength(0)
    $lock.Write($metadata, 0, $metadata.Length)
    $lock.Flush()
    [void](Assert-LocalPath $Queue)
    [void][IO.Directory]::CreateDirectory($Queue)
    if ([IO.File]::Exists($StopFile)) { [IO.File]::Delete($StopFile) }
    Write-Log "dispatcher=start pid=$PID projects=$($Allowed.Count)"
    # A crash between claim and completion has unknown delivery outcome.
    foreach ($file in Get-ChildItem -LiteralPath $Queue -Filter '*.processing' -File) {
        [IO.File]::Move($file.FullName, (Join-Path $Queue ($file.BaseName + '.uncertain')))
        Write-Log "request=$($file.BaseName) status=uncertain reason=interrupted-dispatcher"
    }
    do {
        [void](Assert-LocalPath $Queue)
        foreach ($file in Get-ChildItem -LiteralPath $Queue -Filter '*.req' -File | Sort-Object LastWriteTimeUtc, Name) {
            if ([IO.File]::Exists($StopFile)) { break }
            Invoke-Request $file
        }
        if ($Once -or [IO.File]::Exists($StopFile)) { break }
        Start-Sleep -Seconds $PollSeconds
    } while ($true)
    Write-Log "dispatcher=stop pid=$PID"
} finally {
    if ($lock) { $lock.Dispose() }
}
