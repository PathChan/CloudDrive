$ErrorActionPreference = 'Continue'

$hostName   = 'dccntj002.corp.novocorp.net'
$bindUser   = 'CORP\stjvmssa'
$bindPass   = 'dNF,dzm9n+X.dC76zQSzh,gXwM,Jw9Wg'
$searchBase = 'OU=UserAccounts,OU=CNTJ,OU=Company,DC=corp,DC=novocorp,DC=net'
$username   = 'bwkk'
$userFilter = "(&(objectClass=user)(sAMAccountName=$username))"
$password   = '111111'

Write-Host "=== Test 1: NativeObject bind (no search base) ===" -ForegroundColor Cyan
try {
    $rootPath1 = "LDAP://$hostName"
    $de1 = New-Object DirectoryServices.DirectoryEntry($rootPath1, $bindUser, $bindPass)
    $null = $de1.NativeObject
    Write-Host "  [OK] Bind successful (root only)" -ForegroundColor Green
} catch {
    Write-Host "  [FAIL] $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== Test 2: NativeObject bind (with search base) ===" -ForegroundColor Cyan
try {
    $rootPath2 = "LDAP://$hostName/$searchBase"
    $de2 = New-Object DirectoryServices.DirectoryEntry($rootPath2, $bindUser, $bindPass)
    $null = $de2.NativeObject
    Write-Host "  [OK] Bind successful (with search base)" -ForegroundColor Green
} catch {
    Write-Host "  [FAIL] $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== Test 3: DirectorySearcher.FindOne() (root only) ===" -ForegroundColor Cyan
try {
    $de3 = New-Object DirectoryServices.DirectoryEntry("LDAP://$hostName", $bindUser, $bindPass)
    $searcher3 = New-Object DirectoryServices.DirectorySearcher($de3)
    $searcher3.Filter = $userFilter
    $searcher3.SearchScope = 'Subtree'
    $searcher3.PageSize = 1000
    $r3 = $searcher3.FindOne()
    if ($r3) {
        Write-Host "  [OK] Found user: $($r3.Properties['distinguishedname'][0])" -ForegroundColor Green
    } else {
        Write-Host "  [OK] No result (user not found)" -ForegroundColor Yellow
    }
} catch {
    Write-Host "  [FAIL] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  Stack: $($_.ScriptStackTrace)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "=== Test 4: DirectorySearcher.FindOne() (with search base) ===" -ForegroundColor Cyan
try {
    $de4 = New-Object DirectoryServices.DirectoryEntry("LDAP://$hostName/$searchBase", $bindUser, $bindPass)
    $searcher4 = New-Object DirectoryServices.DirectorySearcher($de4)
    $searcher4.Filter = $userFilter
    $searcher4.SearchScope = 'Subtree'
    $searcher4.PageSize = 1000
    $r4 = $searcher4.FindOne()
    if ($r4) {
        Write-Host "  [OK] Found user: $($r4.Properties['distinguishedname'][0])" -ForegroundColor Green
    } else {
        Write-Host "  [OK] No result (user not found)" -ForegroundColor Yellow
    }
} catch {
    Write-Host "  [FAIL] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  Stack: $($_.ScriptStackTrace)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "=== Test 5: Full flow (bind + search + verify password) ===" -ForegroundColor Cyan
try {
    $de5 = New-Object DirectoryServices.DirectoryEntry("LDAP://$hostName", $bindUser, $bindPass)
    $searcher5 = New-Object DirectoryServices.DirectorySearcher($de5)
    $searcher5.Filter = $userFilter
    $searcher5.SearchScope = 'Subtree'
    $searcher5.PageSize = 1000
    [void]$searcher5.PropertiesToLoad.Add('mail')
    [void]$searcher5.PropertiesToLoad.Add('displayName')
    [void]$searcher5.PropertiesToLoad.Add('distinguishedName')
    [void]$searcher5.PropertiesToLoad.Add('memberOf')

    $r5 = $searcher5.FindOne()
    if (-not $r5) {
        Write-Host "  [FAIL] User not found" -ForegroundColor Red
    } else {
        $userDN = $r5.Properties['distinguishedname'][0].ToString()
        Write-Host "  [OK] User found: $userDN" -ForegroundColor Green
        Write-Host "  mail: $($r5.Properties['mail'][0])" -ForegroundColor Gray
        Write-Host "  displayName: $($r5.Properties['displayname'][0])" -ForegroundColor Gray
        Write-Host "  memberOf: $($r5.Properties['memberof'] -join ', ')" -ForegroundColor Gray

        Write-Host ""
        Write-Host "  Verifying password..." -ForegroundColor Cyan
        try {
            $userEntry = New-Object DirectoryServices.DirectoryEntry("LDAP://$hostName/$userDN", "corp\$username", $password)
            $null = $userEntry.NativeObject
            Write-Host "  [OK] Password correct!" -ForegroundColor Green
        } catch {
            Write-Host "  [FAIL] Password wrong: $($_.Exception.Message)" -ForegroundColor Red
        }
    }
} catch {
    Write-Host "  [FAIL] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  Stack: $($_.ScriptStackTrace)" -ForegroundColor Gray
}
