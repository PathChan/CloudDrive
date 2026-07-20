$ErrorActionPreference = 'Continue'

$hostName   = 'dccntj002.corp.novocorp.net'
$bindUser   = 'CORP\stjvmssa'
$bindPass   = 'dNF,dzm9n+X.dC76zQSzh,gXwM,Jw9Wg'
$searchBase = 'OU=UserAccounts,OU=CNTJ,OU=Company,DC=corp,DC=novocorp,DC=net'
$username   = 'bwkk'

Write-Host "=== Test: Children enumeration ===" -ForegroundColor Cyan
try {
    $ou = New-Object DirectoryServices.DirectoryEntry("LDAP://$hostName/$searchBase", $bindUser, $bindPass)
    Write-Host "  OU Path: $($ou.Path)"
    $null = $ou.NativeObject
    Write-Host "  OU bind OK"
    
    $ou.Children.SchemaFilter.Add('user')
    Write-Host "  Enumerating children..."
    $count = 0
    foreach ($child in $ou.Children) {
        $count++
        $sam = ''
        try { $sam = $child.Properties['sAMAccountName'][0] } catch {}
        if ($sam -eq $username) {
            Write-Host "  [FOUND] $($child.Path)" -ForegroundColor Green
            Write-Host "    sAMAccountName: $sam"
            try { Write-Host "    mail: $($child.Properties['mail'][0])" } catch {}
            try { Write-Host "    displayName: $($child.Properties['displayName'][0])" } catch {}
            try { Write-Host "    distinguishedName: $($child.Properties['distinguishedName'][0])" } catch {}
            try { Write-Host "    userPrincipalName: $($child.Properties['userPrincipalName'][0])" } catch {}
            try { Write-Host "    memberOf: $($child.Properties['memberOf'] -join ', ')" } catch {}
        }
    }
    Write-Host "  Total children: $count"
} catch {
    Write-Host "  [FAIL] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  Stack: $($_.ScriptStackTrace)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "=== Test: Direct CN construct + bind ===" -ForegroundColor Cyan
try {
    $userDN = "CN=$username,$searchBase"
    $userPath = "LDAP://$hostName/$userDN"
    Write-Host "  Trying: $userPath"
    
    $de = New-Object DirectoryServices.DirectoryEntry($userPath, $bindUser, $bindPass)
    $null = $de.NativeObject
    Write-Host "  [OK] User exists!" -ForegroundColor Green
    Write-Host "    Path: $($de.Path)"
    Write-Host "    Name: $($de.Name)"
    try { Write-Host "    mail: $($de.Properties['mail'][0])" } catch {}
    try { Write-Host "    displayName: $($de.Properties['displayName'][0])" } catch {}
    try { Write-Host "    distinguishedName: $($de.Properties['distinguishedName'][0])" } catch {}
    try { Write-Host "    memberOf: $($de.Properties['memberOf'] -join ', ')" } catch {}
} catch {
    Write-Host "  [FAIL] $($_.Exception.Message)" -ForegroundColor Red
}
