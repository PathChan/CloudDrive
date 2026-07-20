$ErrorActionPreference = 'Continue'

$hostName   = 'dccntj002.corp.novocorp.net'
$bindUser   = 'CORP\stjvmssa'
$bindPass   = 'dNF,dzm9n+X.dC76zQSzh,gXwM,Jw9Wg'
$searchBase = 'OU=UserAccounts,OU=CNTJ,OU=Company,DC=corp,DC=novocorp,DC=net'
$username   = 'bwkk'
$userFilter = "(&(objectClass=user)(sAMAccountName=$username))"
$password   = '111111'

Write-Host "=== Test GC:// (Global Catalog) ===" -ForegroundColor Cyan
try {
    $de = New-Object DirectoryServices.DirectoryEntry("GC://$hostName", $bindUser, $bindPass)
    $searcher = New-Object DirectoryServices.DirectorySearcher($de)
    $searcher.Filter = $userFilter
    $searcher.SearchScope = 'Subtree'
    $searcher.PageSize = 1000
    [void]$searcher.PropertiesToLoad.Add('mail')
    [void]$searcher.PropertiesToLoad.Add('displayName')
    [void]$searcher.PropertiesToLoad.Add('distinguishedName')
    [void]$searcher.PropertiesToLoad.Add('memberOf')
    $r = $searcher.FindOne()
    if ($r) {
        $userDN = $r.Properties['distinguishedname'][0].ToString()
        Write-Host "  [OK] Found: $userDN" -ForegroundColor Green
        Write-Host "  mail: $($r.Properties['mail'][0])"
        Write-Host "  memberOf: $($r.Properties['memberof'] -join ', ')"
        
        # Verify password
        Write-Host "  Verifying password..." -ForegroundColor Cyan
        try {
            $ue = New-Object DirectoryServices.DirectoryEntry("LDAP://$hostName/$userDN", "corp\$username", $password)
            $null = $ue.NativeObject
            Write-Host "  [OK] Password correct!" -ForegroundColor Green
        } catch {
            Write-Host "  [FAIL] Password: $($_.Exception.Message)" -ForegroundColor Red
        }
    } else {
        Write-Host "  [OK] No user found" -ForegroundColor Yellow
    }
} catch {
    Write-Host "  [FAIL] $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== Test serverless bind ===" -ForegroundColor Cyan
try {
    $de2 = New-Object DirectoryServices.DirectoryEntry("LDAP://$searchBase", $bindUser, $bindPass)
    $searcher2 = New-Object DirectoryServices.DirectorySearcher($de2)
    $searcher2.Filter = $userFilter
    $searcher2.SearchScope = 'Subtree'
    $searcher2.PageSize = 1000
    $r2 = $searcher2.FindOne()
    if ($r2) {
        Write-Host "  [OK] Found: $($r2.Properties['distinguishedname'][0])" -ForegroundColor Green
    } else {
        Write-Host "  [OK] No user found" -ForegroundColor Yellow
    }
} catch {
    Write-Host "  [FAIL] $($_.Exception.Message)" -ForegroundColor Red
}
