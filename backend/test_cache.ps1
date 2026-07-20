$ErrorActionPreference = 'Continue'

$hostName   = 'dccntj002.corp.novocorp.net'
$bindUser   = 'CORP\stjvmssa'
$bindPass   = 'dNF,dzm9n+X.dC76zQSzh,gXwM,Jw9Wg'
$searchBase = 'OU=UserAccounts,OU=CNTJ,OU=Company,DC=corp,DC=novocorp,DC=net'

Write-Host "=== Test: CN construct + RefreshCache ===" -ForegroundColor Cyan
$userDN = "CN=bwkk,OU=UserAccounts,OU=CNTJ,OU=Company,DC=corp,DC=novocorp,DC=net"
$userPath = "LDAP://$hostName/$userDN"
Write-Host "Path: $userPath"

$de = New-Object DirectoryServices.DirectoryEntry($userPath, $bindUser, $bindPass)
$null = $de.NativeObject
Write-Host "NativeObject OK"

# RefreshCache to load properties
try {
    $de.RefreshCache(@('mail','displayName','userPrincipalName','distinguishedName','memberOf','sAMAccountName','cn'))
    Write-Host "RefreshCache OK"
    
    Write-Host "--- Properties ---"
    function Get-Prop($obj, $name) {
        try {
            if ($obj.Properties[$name] -and $obj.Properties[$name].Count -gt 0) {
                return "$name = $($obj.Properties[$name][0])"
            }
        } catch {}
        return "$name = (empty)"
    }
    Write-Host (Get-Prop $de 'mail')
    Write-Host (Get-Prop $de 'displayName')
    Write-Host (Get-Prop $de 'userPrincipalName')
    Write-Host (Get-Prop $de 'distinguishedName')
    Write-Host (Get-Prop $de 'cn')
    Write-Host (Get-Prop $de 'sAMAccountName')
    
    Write-Host "--- memberOf ---"
    try {
        $memberOf = $de.Properties['memberOf']
        if ($memberOf -and $memberOf.Count -gt 0) {
            for ($i=0; $i -lt $memberOf.Count; $i++) {
                Write-Host "  [$i] $($memberOf[$i])"
            }
        } else {
            Write-Host "  (none)"
        }
    } catch {
        Write-Host "  (error)"
    }
} catch {
    Write-Host "RefreshCache FAIL: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== Test: Password verify ===" -ForegroundColor Cyan
try {
    $passEntry = New-Object DirectoryServices.DirectoryEntry($userPath, "corp\bwkk", "111111")
    $null = $passEntry.NativeObject
    Write-Host "  [OK] Password CORRECT" -ForegroundColor Green
} catch {
    Write-Host "  [FAIL] Password WRONG: $($_.Exception.Message)" -ForegroundColor Red
}

# Test wrong password
try {
    $passEntry2 = New-Object DirectoryServices.DirectoryEntry($userPath, "corp\bwkk", "wrongpassword")
    $null = $passEntry2.NativeObject
    Write-Host "  [BUG] Wrong password accepted!" -ForegroundColor Red
} catch {
    Write-Host "  [OK] Wrong password correctly rejected: $($_.Exception.Message.Split([Environment]::NewLine)[0])" -ForegroundColor Green
}
