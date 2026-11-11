$ErrorActionPreference = 'Stop'
$now = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([datetime]::UtcNow, 'Asia/Seoul')
$rows = [System.Collections.Generic.List[object]]::new()
for ($offset = 0; $offset -lt 1; $offset++) {
  $date = $now.AddDays(-$offset).ToString('yyyyMMdd')
  $page = 1
  do {
    $url = "https://api-gw.sports.naver.com/news/articles/kbaseball?team=OB&sort=latest&date=$date&page=$page&pageSize=40"
    Write-Host "Fetching Doosan news date=$date page=$page"
    $r = Invoke-RestMethod -Uri $url -Headers @{Referer='https://m.sports.naver.com/';'User-Agent'='Mozilla/5.0'} -TimeoutSec 20 -MaximumRetryCount 2 -RetryIntervalSec 2
    if (-not $r.success -or $null -eq $r.result.newsList) { throw 'Invalid Naver Sports response' }
    foreach ($article in $r.result.newsList) { $rows.Add($article) }
    if ($page * 40 -ge $r.result.totalCount) { break }
    if ($r.result.newsList.Count -eq 0 -or $page -ge 100) { throw 'Incomplete pagination' }
    $page++
  } while ($true)
}
ConvertTo-Json -InputObject @($rows.ToArray()) -Depth 15 | Set-Content -LiteralPath 'doosan_snapshot.json' -Encoding utf8
Write-Host "DOOSAN_CLOUD_OK collected=$($rows.Count)"
