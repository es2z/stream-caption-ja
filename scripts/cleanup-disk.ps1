<#
.SYNOPSIS
    Docker の不要なデータを消し、Docker の仮想ディスクを圧縮して Windows に空き容量を返す。

.DESCRIPTION
    1. 使われなくなった古いイメージ(<none>)を消す
    2. 今のイメージで使っていない古いビルドキャッシュを消す
       (uv のダウンロードキャッシュは残す。-IncludeDownloadCache を付けると消す)
    3. Docker Desktop と WSL を止めて仮想ディスク(docker_data.vhdx)を圧縮し、Docker Desktop を起動し直す
       (数分かかる。字幕アプリは restart: unless-stopped なので自動で立ち上がる)

    消さないもの: 今のイメージ、モデル(ボリューム)、.env / config.yaml

    Docker の仮想ディスクは、中身を消しても Windows 側のファイルが小さくならないため 3 の圧縮が要る。
    圧縮には管理者権限が必要で、管理者でなければ圧縮の手前で確認画面が出る。

.EXAMPLE
    # Windows の PowerShell で実行する(WSL のシェルからは実行しない。途中で WSL を止めるため)
    powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu-24.04\home\<ユーザー名>\stream-caption-ja\scripts\cleanup-disk.ps1
#>
[CmdletBinding()]
param(
    # uv のダウンロードキャッシュ(約 7GB)も消す。次の --build で PyTorch などを再ダウンロードする
    [switch]$IncludeDownloadCache,
    # Docker を止めずに中の掃除だけする(Windows 側の空きは増えない。管理者権限も不要)
    [switch]$SkipCompact,
    # 開始前の確認を省く
    [switch]$Yes,
    # Docker Desktop の仮想ディスクの場所(普段は自動で見つける)
    [string]$VhdxPath,
    # 内部用: 管理者として起動し直し、このファイルを圧縮するだけのとき
    [string]$CompactOnly
)

$ErrorActionPreference = 'Stop'

function Write-Step([string]$Message) {
    Write-Host ''
    Write-Host "== $Message" -ForegroundColor Cyan
}

# 外部コマンドを実行して、出力と終了コードを返す。
# Windows PowerShell 5.1 は Stop のまま標準エラーをリダイレクトすると例外にするので、ここだけ Continue にする
function Invoke-Native([string]$File, [string[]]$Arguments) {
    $ErrorActionPreference = 'Continue'
    $output = & $File @Arguments 2>&1 | ForEach-Object { "$_" }
    [pscustomobject]@{ Code = $LASTEXITCODE; Output = @($output) }
}

function Test-Admin {
    $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Format-GB([double]$Bytes) { '{0:N1} GB' -f ($Bytes / 1GB) }

function Get-FreeBytes([string]$Path) {
    (Get-PSDrive -Name (Split-Path -Qualifier $Path).TrimEnd(':')).Free
}

# WSL の登録情報から docker-desktop の場所を調べ、その隣の disk\docker_data.vhdx を探す
function Find-DockerVhdx {
    $lxss = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
    foreach ($key in @(Get-ChildItem -Path $lxss -ErrorAction SilentlyContinue)) {
        $item = Get-ItemProperty -LiteralPath $key.PSPath
        if ($item.DistributionName -eq 'docker-desktop' -and $item.BasePath) {
            $base = $item.BasePath -replace '^\\\\\?\\', ''
            $candidate = Join-Path (Split-Path $base -Parent) 'disk\docker_data.vhdx'
            if (Test-Path -LiteralPath $candidate) { return $candidate }
        }
    }
    $default = Join-Path $env:LOCALAPPDATA 'Docker\wsl\disk\docker_data.vhdx'
    if (Test-Path -LiteralPath $default) { return $default }
    $null
}

function Wait-FileUnlocked([string]$Path, [int]$Seconds = 60) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            [IO.File]::Open($Path, 'Open', 'Read', 'None').Close()
            return $true
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    $false
}

function Invoke-Diskpart([string[]]$Lines) {
    $commands = Join-Path $env:TEMP 'stream-caption-diskpart.txt'
    [IO.File]::WriteAllLines($commands, $Lines)
    try {
        diskpart /s $commands | Out-Host
        $LASTEXITCODE
    } finally {
        Remove-Item -LiteralPath $commands -ErrorAction SilentlyContinue
    }
}

function Compress-Vhdx([string]$Path) {
    if (Get-Command Optimize-VHD -ErrorAction SilentlyContinue) {
        Mount-VHD -Path $Path -ReadOnly
        try { Optimize-VHD -Path $Path -Mode Full }
        finally { Dismount-VHD -Path $Path }
        return
    }
    # Hyper-V の PowerShell モジュールがない環境(Windows Home など)は diskpart を使う
    $select = "select vdisk file=`"$Path`""
    $code = Invoke-Diskpart @($select, 'attach vdisk readonly', 'compact vdisk', 'detach vdisk')
    if ($code -ne 0) {
        # 途中で失敗するとつないだままになり、Docker が起動できなくなるので外しておく
        [void](Invoke-Diskpart @($select, 'detach vdisk'))
        throw "diskpart が失敗しました(終了コード $code)"
    }
}

# --- 内部用: 管理者として起動し直されたときは圧縮だけする ----------------------
if ($CompactOnly) {
    try {
        Compress-Vhdx $CompactOnly
        exit 0
    } catch {
        Write-Host "圧縮に失敗しました: $_" -ForegroundColor Red
        Read-Host 'Enter キーで閉じます' | Out-Null
        exit 1
    }
}

function Invoke-Compaction([string]$Path) {
    if (Test-Admin) {
        Compress-Vhdx $Path
        return
    }
    # \\wsl.localhost 上のファイルは管理者のプロセスから開けないことがあるので、一時フォルダに写してから起動する
    $copy = Join-Path $env:TEMP 'stream-caption-cleanup-disk.ps1'
    Copy-Item -LiteralPath $PSCommandPath -Destination $copy -Force
    Write-Host '圧縮には管理者権限が必要です。確認画面が出たら「はい」を選んでください'
    try {
        $proc = Start-Process -FilePath (Get-Process -Id $PID).Path -Verb RunAs -Wait -PassThru -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$copy`"", '-CompactOnly', "`"$Path`"")
    } finally {
        Remove-Item -LiteralPath $copy -ErrorAction SilentlyContinue
    }
    if ($proc.ExitCode -ne 0) { throw "圧縮に失敗しました(終了コード $($proc.ExitCode))" }
}

function Test-DockerRunning {
    (Invoke-Native docker @('info', '--format', '{{.ServerVersion}}')).Code -eq 0
}

function Start-DockerDesktop {
    if ((Invoke-Native docker @('desktop', 'start')).Code -ne 0) {
        # docker desktop コマンドがない古い版では、アプリを直接起動する
        $bin = Split-Path (Get-Command docker).Source -Parent
        $app = @(
            (Join-Path $bin '..\..\Docker Desktop.exe'),
            (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'),
            (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe')
        ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if ($app) { Start-Process -FilePath $app }
    }
    $deadline = (Get-Date).AddMinutes(5)
    while ((Get-Date) -lt $deadline) {
        if (Test-DockerRunning) { return $true }
        Start-Sleep -Seconds 5
    }
    $false
}

function Stop-DockerDesktop {
    if ((Invoke-Native docker @('desktop', 'stop')).Code -ne 0) {
        Get-Process -Name 'Docker Desktop', 'com.docker.backend', 'com.docker.build' -ErrorAction SilentlyContinue |
            Stop-Process -Force
    }
}

# 今のイメージと共有していない古いビルドキャッシュ
function Get-StaleBuildCache {
    $result = Invoke-Native docker @('buildx', 'du', '--format', 'json')
    if ($result.Code -ne 0) { return @() }
    @($result.Output | Where-Object { $_.StartsWith('{') } | ForEach-Object { $_ | ConvertFrom-Json } |
        Where-Object {
            -not $_.Shared -and ($_.Type -eq 'regular' -or ($IncludeDownloadCache -and $_.Type -eq 'exec.cachemount'))
        })
}

function Remove-StaleBuildCache {
    # キャッシュには親子関係があり、子が残っている親は消せないので、減らなくなるまで繰り返す
    for ($pass = 0; $pass -lt 30; $pass++) {
        $targets = @(Get-StaleBuildCache)
        if ($targets.Count -eq 0) { return }
        foreach ($target in $targets) {
            [void](Invoke-Native docker @('builder', 'prune', '-f', '--filter', "id=$($target.ID)"))
        }
        if (@(Get-StaleBuildCache).Count -ge $targets.Count) { return }
    }
}

# Docker の VM の中で fstrim を実行し、消したデータの場所を仮想ディスクに知らせる(圧縮で縮むようになる)
function Invoke-Fstrim([string]$Image) {
    $nsenter = @('-t', '1', '-m', '--', 'fstrim', '-a')
    $run = @('run', '--rm', '--privileged', '--pid=host')
    if ((Invoke-Native docker ($run + @('--entrypoint', 'nsenter', $Image) + $nsenter)).Code -eq 0) { return }

    # アプリのイメージがまだない場合は小さなイメージで代用し、使い終わったら消す
    $helper = 'alpine:3.20'
    $hadHelper = (Invoke-Native docker @('image', 'inspect', $helper)).Code -eq 0
    $result = Invoke-Native docker ($run + @($helper, 'nsenter') + $nsenter)
    if (-not $hadHelper) { [void](Invoke-Native docker @('rmi', $helper)) }
    if ($result.Code -ne 0) { Write-Warning 'fstrim に失敗しました。圧縮で縮む量が少なくなるかもしれません' }
}

# --- 本体 -----------------------------------------------------------------------
$repoRoot = Split-Path $PSScriptRoot -Parent
$appImage = (Split-Path $repoRoot -Leaf).ToLower() + '-app'
$dockerStopped = $false

# WSL 上のフォルダにいると WSL を止めたときに困るので、Windows 側のフォルダに移っておく
Push-Location $env:USERPROFILE
try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'docker コマンドが見つかりません。Docker Desktop を入れてください'
    }
    if (-not $VhdxPath) { $VhdxPath = Find-DockerVhdx }
    if (-not $SkipCompact -and -not ($VhdxPath -and (Test-Path -LiteralPath $VhdxPath))) {
        throw 'Docker の仮想ディスク(docker_data.vhdx)が見つかりません。-VhdxPath で場所を指定するか、-SkipCompact を付けて実行してください'
    }

    if (-not $SkipCompact -and -not $Yes) {
        Write-Host '途中で Docker Desktop と WSL を数分止めます(字幕アプリも止まり、終わると自動で立ち上がります)。'
        Write-Host 'WSL のターミナルやエディタは閉じられるので、作業中のものは保存してから続けてください。'
        if ((Read-Host '続けますか? [y/N]') -notmatch '^[yY]') {
            Write-Host '中止しました'
            return
        }
    }

    if (-not (Test-DockerRunning)) {
        Write-Step 'Docker Desktop を起動しています'
        if (-not (Start-DockerDesktop)) { throw 'Docker Desktop が起動しません' }
    }

    if ($VhdxPath) {
        $vhdxBefore = (Get-Item -LiteralPath $VhdxPath).Length
        $freeBefore = Get-FreeBytes $VhdxPath
    }

    Write-Step '使われなくなった古いイメージを消しています'
    (Invoke-Native docker @('image', 'prune', '-f')).Output | Select-Object -Last 1 | Out-Host

    Write-Step '古いビルドキャッシュを消しています'
    Remove-StaleBuildCache
    (Invoke-Native docker @('system', 'df')).Output | Out-Host

    if ($SkipCompact) {
        Write-Host ''
        Write-Host 'Docker の中の掃除だけしました。Windows 側の空きを増やすには -SkipCompact を付けずに実行してください。'
        return
    }

    Write-Step '仮想ディスクの空き領域を整理しています(fstrim)'
    Invoke-Fstrim $appImage

    Write-Step 'Docker Desktop と WSL を止めています'
    $dockerStopped = $true
    Stop-DockerDesktop
    [void](Invoke-Native wsl.exe @('--shutdown'))
    if (-not (Wait-FileUnlocked $VhdxPath)) {
        throw '仮想ディスクが使用中のままです。少し待ってからもう一度実行してください'
    }

    Write-Step "仮想ディスクを圧縮しています(数分かかります): $VhdxPath"
    Invoke-Compaction $VhdxPath
} finally {
    if ($dockerStopped) {
        Write-Step 'Docker Desktop を起動しています'
        if (-not (Start-DockerDesktop)) {
            Write-Warning 'Docker Desktop が起動しません。手動で起動してください'
        }
    }
    Pop-Location
}

Write-Step '結果'
Write-Host ('Docker の仮想ディスク: {0} → {1}' -f (Format-GB $vhdxBefore), (Format-GB (Get-Item -LiteralPath $VhdxPath).Length))
Write-Host ('{0} ドライブの空き: {1} → {2}' -f (Split-Path -Qualifier $VhdxPath), (Format-GB $freeBefore), (Format-GB (Get-FreeBytes $VhdxPath)))
Write-Host '字幕アプリは、Docker Desktop の起動から 1 分ほどで使えるようになります(docker compose logs -f app で確認できます)。'
