<#
.SYNOPSIS
Move folders and files into the drive's archive or trash, keeping their path.

.DESCRIPTION
PowerShell version of g-drive-cleanup.py; the two behave the same and write the same cleanup.log.

The drive root is the nearest folder, from the current directory upwards, that holds both
0_ARCHIVE___ and 0_TRASH___. Paths are kept relative to that root, so running in
<root>\Users\x\Projects\A:

    g-drive-cleanup.ps1 -Archive cfd docs

moves cfd and docs to <root>\0_ARCHIVE___\Users\x\Projects\A\. This works the same whatever
the drive is mounted as (G:\, /mnt/q, ...). -Restore moves them back. Every move is appended
to cleanup.log in the archive or trash folder.

.EXAMPLE
g-drive-cleanup.ps1 -Trash old-runs -DryRun

.EXAMPLE
g-drive-cleanup.ps1 -Restore cfd -From archive
#>
[CmdletBinding()]
param(
    [Parameter(ParameterSetName = 'Archive', Mandatory)] [switch] $Archive,
    [Parameter(ParameterSetName = 'Trash', Mandatory)] [switch] $Trash,
    # Move PATHs back; give them as they were before, e.g. run in the folder they were moved from
    [Parameter(ParameterSetName = 'Restore', Mandatory)] [switch] $Restore,
    # With -Restore: where to take PATH from if it is in both
    [Parameter(ParameterSetName = 'Restore')] [ValidateSet('archive', 'trash')] [string] $From,
    # If the target exists, add a timestamp to the name instead of failing
    [switch] $KeepBoth,
    # Only show what would be moved
    [Alias('n')] [switch] $DryRun,
    [Parameter(Mandatory, Position = 0, ValueFromRemainingArguments)] [string[]] $Paths
)

$ErrorActionPreference = 'Stop'

# Not $Archive/$Trash: PowerShell variable names ignore case, so those are the switches above
$ArchiveDir = '0_ARCHIVE___'
$TrashDir = '0_TRASH___'
$Markers = @($ArchiveDir, $TrashDir)
$LogName = 'cleanup.log'
$Sep = [IO.Path]::DirectorySeparatorChar


function Join([string] $Parent, [string] $Child) {
    return [IO.Path]::Combine($Parent, $Child)
}


function Get-AbsolutePath([string] $Path) {
    # Relative to PowerShell's location, which is not the process's current directory.
    # GetFullPath only tidies the text (.., /); it never turns G:\ into \\server\share.
    $full = [IO.Path]::GetFullPath((Join $PWD.ProviderPath $Path))
    $trimmed = $full.TrimEnd('\', '/')
    if ($trimmed -eq '' -or $trimmed.EndsWith(':')) { return $full }  # keep "G:\" and "/"
    return $trimmed
}


function Test-Inside([string] $Path, [string] $Folder) {
    return ($Path -ieq $Folder) -or
        $Path.StartsWith($Folder.TrimEnd('\', '/') + $Sep, [StringComparison]::OrdinalIgnoreCase)
}


function Find-Root([string] $Start) {
    $folder = $Start
    while ($folder) {
        $found = @($Markers | Where-Object { Test-Path -LiteralPath (Join $folder $_) -PathType Container })
        if ($found.Count -eq $Markers.Count) { return $folder }
        $folder = [IO.Path]::GetDirectoryName($folder)  # empty above the drive root
    }
    throw "no folder above $Start holds both $ArchiveDir and $TrashDir"
}


function Get-PathBelow([string] $Path, [string] $Root) {
    if (-not (Test-Inside $Path $Root)) { throw "$Path is outside the drive root $Root" }
    return $Path.Substring([Math]::Min($Path.Length, $Root.TrimEnd('\', '/').Length + 1))
}


function Get-RelativePath([string] $Path, [string] $Root) {
    $rel = Get-PathBelow $Path $Root
    if (-not $rel) { throw 'refusing to move the drive root itself' }
    $first = $rel.Split([char[]]@('\', '/'))[0]
    if ($Markers -icontains $first) { throw "$Path is already inside $first" }
    return $rel
}


function Get-FreeName([string] $Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $Path }
    if (-not $KeepBoth) { throw "$Path already exists (use -KeepBoth to add a timestamp)" }
    return $Path + '.' + (Get-Date -Format "yyyy-MM-dd'T'HHmmss")
}


function Write-CleanupLog([string] $MarkerDir, [string] $Op, [string] $Src, [string] $Dst, [string] $Root) {
    $line = @(
        (Get-Date -Format "yyyy-MM-dd'T'HH:mm:ss"), [Environment]::UserName, $Op,
        ((Get-PathBelow $Src $Root) -replace '\\', '/'), ((Get-PathBelow $Dst $Root) -replace '\\', '/')
    ) -join "`t"
    # UTF-8 without BOM, same as the Python version
    [IO.File]::AppendAllText((Join $MarkerDir $LogName), $line + [Environment]::NewLine,
        (New-Object Text.UTF8Encoding $false))
}


function Invoke-Move([string] $Src, [string] $Dst) {
    Write-Host "$Src`n  -> $Dst"
    if ($DryRun) { return }
    [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Dst))
    # Directory.Move, not Move-Item: it fails instead of copying when Src and Dst are on
    # different drives, so a move is always a quick rename on the server. It moves files too.
    try {
        [IO.Directory]::Move($Src, $Dst)
    } catch {
        throw "could not move ${Src}: $($_.Exception.GetBaseException().Message)"
    }
}


function Remove-EmptyParents([string] $Folder, [string] $Stop) {
    # Drop the now empty parent folders that were only made to hold the moved item
    while ($Folder -and -not ($Folder -ieq $Stop) -and (Test-Inside $Folder $Stop)) {
        try { [IO.Directory]::Delete($Folder) } catch { return }  # fails when not empty
        $Folder = [IO.Path]::GetDirectoryName($Folder)
    }
}


function Invoke-PutAway([string] $Path, [string] $Root, [string] $Marker) {
    $rel = Get-RelativePath $Path $Root
    if (-not (Test-Path -LiteralPath $Path)) { throw "$Path does not exist" }
    if (Test-Inside $Cwd $Path) { throw "$Path holds the current directory; cd out of it first" }
    $dst = Get-FreeName (Join (Join $Root $Marker) $rel)
    Invoke-Move $Path $dst
    if (-not $DryRun) { Write-CleanupLog (Join $Root $Marker) $Marker.Trim([char[]]'0_') $Path $dst $Root }
}


function Invoke-Restore([string] $Path, [string] $Root) {
    $rel = Get-RelativePath $Path $Root
    $found = @($Markers | Where-Object { Test-Path -LiteralPath (Join (Join $Root $_) $rel) })
    if ($found.Count -eq 0) { throw "$rel is in neither $ArchiveDir nor $TrashDir" }
    if ($found.Count -gt 1) {
        if (-not $From) { throw "$rel is in both; choose with -From archive|trash" }
        $found = @(if ($From -eq 'archive') { $ArchiveDir } else { $TrashDir })
    }
    $marker = $found[0]
    $src = Join (Join $Root $marker) $rel
    $dst = Get-FreeName $Path
    Invoke-Move $src $dst
    if (-not $DryRun) {
        Write-CleanupLog (Join $Root $marker) 'RESTORE' $src $dst $Root
        Remove-EmptyParents ([IO.Path]::GetDirectoryName($src)) (Join $Root $marker)
    }
}


$Cwd = Get-AbsolutePath '.'
try {
    $root = Find-Root $Cwd
} catch {
    [Console]::Error.WriteLine("error: $($_.Exception.Message)")
    exit 1
}
Write-Host "Drive root: $root"

$failed = 0
foreach ($p in $Paths) {
    try {
        $full = Get-AbsolutePath $p
        switch ($PSCmdlet.ParameterSetName) {
            'Archive' { Invoke-PutAway $full $root $ArchiveDir }
            'Trash' { Invoke-PutAway $full $root $TrashDir }
            'Restore' { Invoke-Restore $full $root }
        }
    } catch {
        [Console]::Error.WriteLine("error: $($_.Exception.Message)")
        $failed++
    }
}
exit $(if ($failed) { 1 } else { 0 })
