# Backup diario de la base PROYECTO_ATC (SQL Server Express, sin SQL Agent
# disponible, por eso esto se programa via Tarea Programada de Windows en
# vez de un job de Agent). Genera un .bak con BACKUP DATABASE, lo comprime a
# .zip (Express no soporta WITH COMPRESSION, es feature de Standard+) con
# las clases de .NET (Compress-Archive no existe en PowerShell 4.0, la
# version instalada en este server), borra el .bak sin comprimir, y aplica
# retencion borrando backups mas viejos que $RetentionDays.
#
# Se agrega solo PROYECTO_ATC -- BDATC no se usa (ver CLAUDE.md) y queda
# fuera a proposito.

$ErrorActionPreference = "Stop"

$DatabaseName = "PROYECTO_ATC"
$BackupDir = "D:\BackUpSQLServer"
$RetentionDays = 30
$LogFile = Join-Path $BackupDir "backup_sql.log"

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $Message"
    Add-Content -Path $LogFile -Value $line
    Write-Output $line
}

if (-not (Test-Path $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir | Out-Null
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$bakPath = Join-Path $BackupDir "${DatabaseName}_$stamp.bak"
$zipPath = Join-Path $BackupDir "${DatabaseName}_$stamp.zip"
$sqlScript = Join-Path $env:TEMP "atc_backup_$stamp.sql"
$sqlOut = Join-Path $env:TEMP "atc_backup_${stamp}_out.txt"

try {
    Write-Log "=== Iniciando backup de $DatabaseName -> $bakPath ==="

    $bakPathSql = $bakPath.Replace("'", "''")
    @"
BACKUP DATABASE [$DatabaseName]
TO DISK = N'$bakPathSql'
WITH INIT, CHECKSUM, STATS = 10;
"@ | Set-Content -Path $sqlScript -Encoding UTF8

    & sqlcmd -S "localhost\SQLEXPRESS" -E -i $sqlScript -o $sqlOut -w 200
    $sqlExit = $LASTEXITCODE
    $sqlOutput = Get-Content $sqlOut -Raw -ErrorAction SilentlyContinue
    Write-Log "sqlcmd exit code: $sqlExit"
    if ($sqlOutput) { Write-Log "sqlcmd output: $sqlOutput" }

    if ($sqlExit -ne 0 -or -not (Test-Path $bakPath)) {
        throw "BACKUP DATABASE fallo (exit=$sqlExit) o no se genero el archivo .bak"
    }
    $bakSizeMB = [math]::Round((Get-Item $bakPath).Length / 1MB, 1)
    Write-Log "Backup .bak generado OK: $bakSizeMB MB"

    Write-Log "Comprimiendo a $zipPath ..."
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::Open($zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $zip, $bakPath, (Split-Path $bakPath -Leaf), [System.IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
    } finally {
        $zip.Dispose()
    }

    $zipSizeMB = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
    Write-Log "Compresion OK: $zipSizeMB MB (de $bakSizeMB MB, $([math]::Round(100 - ($zipSizeMB/$bakSizeMB*100),1))% de reduccion)"

    Remove-Item $bakPath -Force
    Write-Log "Borrado .bak sin comprimir (queda solo el .zip)"

    $cutoff = (Get-Date).AddDays(-$RetentionDays)
    $viejos = Get-ChildItem $BackupDir -Filter "${DatabaseName}_*.zip" | Where-Object { $_.LastWriteTime -lt $cutoff }
    foreach ($f in $viejos) {
        Remove-Item $f.FullName -Force
        Write-Log "Borrado por retencion ($RetentionDays dias): $($f.Name)"
    }

    $totalBackups = (Get-ChildItem $BackupDir -Filter "${DatabaseName}_*.zip" | Measure-Object).Count
    $totalSizeMB = [math]::Round((Get-ChildItem $BackupDir -Filter "${DatabaseName}_*.zip" | Measure-Object -Property Length -Sum).Sum / 1MB, 1)
    Write-Log "=== Backup completado OK. Total en $BackupDir : $totalBackups backups, $totalSizeMB MB ==="
} catch {
    Write-Log "ERROR: $($_.Exception.Message)"
    throw
} finally {
    Remove-Item $sqlScript -ErrorAction SilentlyContinue
    Remove-Item $sqlOut -ErrorAction SilentlyContinue
}
