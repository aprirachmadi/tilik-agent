param(
    [string]$OutputDirectory = "docs/research/bps-api-snapshot-2026-09-11"
)

$ErrorActionPreference = "Stop"

function Get-DotEnvValue {
    param([string]$Name)
    $line = Get-Content -LiteralPath ".env" |
        Where-Object { $_ -match "^\s*$([regex]::Escape($Name))\s*=" } |
        Select-Object -First 1
    if (-not $line) { throw "Required environment variable is missing from .env" }
    $value = ($line -split "=", 2)[1].Trim()
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'"))) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    if ([string]::IsNullOrWhiteSpace($value)) { throw "Required environment variable is empty" }
    return $value
}

function Invoke-BpsApi {
    param([Parameter(Mandatory)][string]$RelativePath)
    $uri = "https://webapi.bps.go.id/v1/api/$RelativePath/key/$script:ApiKey"
    try {
        $response = Invoke-RestMethod -Uri $uri -Method Get -TimeoutSec 60
    }
    catch {
        throw "BPS API request failed for redacted path: $RelativePath"
    }
    if ($response.status -eq "Error") {
        throw "BPS API returned an error for redacted path: $RelativePath ($($response.message))"
    }
    return $response
}

function Get-RecordsAndMeta {
    param([Parameter(Mandatory)]$Response)
    $payload = $Response.data
    if (-not $payload -or $payload.Count -lt 2) {
        $Response | ConvertTo-Json -Depth 8 |
            Set-Content -LiteralPath (Join-Path $outputPath "unexpected-response.json") -Encoding utf8
        throw "Unexpected BPS list response shape; saved public response for inspection"
    }
    return @{
        Meta = $payload[0]
        Records = @($payload[1])
    }
}

$script:ApiKey = if ($env:BPS_API_KEY) { $env:BPS_API_KEY } else { Get-DotEnvValue -Name "BPS_API_KEY" }
$outputPath = Join-Path (Get-Location) $OutputDirectory
New-Item -ItemType Directory -Force -Path $outputPath | Out-Null

$subjectResponse = Invoke-BpsApi -RelativePath "list/model/subjectcsa/domain/0000/subcat/514/page/1"
$subjectResult = Get-RecordsAndMeta -Response $subjectResponse
$subjects = [System.Collections.Generic.List[object]]::new()
foreach ($record in $subjectResult.Records) { $subjects.Add($record) }
for ($page = 2; $page -le [int]$subjectResult.Meta.pages; $page++) {
    $pageResponse = Invoke-BpsApi -RelativePath "list/model/subjectcsa/domain/0000/subcat/514/page/$page"
    $pageResult = Get-RecordsAndMeta -Response $pageResponse
    foreach ($record in $pageResult.Records) { $subjects.Add($record) }
}

$allTables = [System.Collections.Generic.List[object]]::new()
$subjectCounts = [System.Collections.Generic.List[object]]::new()
foreach ($subject in $subjects) {
    $subjectId = [string]$subject.sub_id
    if (-not $subjectId) { $subjectId = [string]$subject.id }
    if (-not $subjectId) { throw "Could not identify a subject ID" }

    $firstResponse = Invoke-BpsApi -RelativePath "list/model/tablestatistic/domain/0000/subject/$subjectId/page/1/perpage/100"
    $firstResult = Get-RecordsAndMeta -Response $firstResponse
    $meta = $firstResult.Meta
    foreach ($record in $firstResult.Records) { $allTables.Add($record) }
    for ($page = 2; $page -le [int]$meta.pages; $page++) {
        $pageResponse = Invoke-BpsApi -RelativePath "list/model/tablestatistic/domain/0000/subject/$subjectId/page/$page/perpage/100"
        $pageResult = Get-RecordsAndMeta -Response $pageResponse
        foreach ($record in $pageResult.Records) { $allTables.Add($record) }
    }

    $subjectCounts.Add([pscustomobject]@{
        subject_id = $subjectId
        subject = if ($subject.title) { $subject.title } elseif ($subject.sub) { $subject.sub } else { $subject.subject }
        total = [int]$meta.total
        pages_at_perpage_100 = [int]$meta.pages
    })
}

$sourceCounts = $allTables |
    Group-Object -Property tablesource |
    Sort-Object -Property Name |
    ForEach-Object { [pscustomobject]@{ tablesource = [int]$_.Name; count = $_.Count } }

$catalog = [pscustomobject]@{
    retrieved_at = (Get-Date).ToUniversalTime().ToString("o")
    api = "BPS WebAPI v1"
    domain = "0000"
    csa_subcategory_id = 514
    subject_pagination = $subjectResult.Meta
    subjects = $subjects
    subject_table_counts = $subjectCounts
    table_source_counts = $sourceCounts
    tables = $allTables
}

$catalog | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $outputPath "catalog.json") -Encoding utf8

$summary = [pscustomobject]@{
    subjects_returned = $subjects.Count
    subject_api_total = $subjectResult.Meta.total
    tables_returned = $allTables.Count
    per_subject = $subjectCounts
    per_source = $sourceCounts
    output = (Join-Path $OutputDirectory "catalog.json")
}
$summary | ConvertTo-Json -Depth 8
