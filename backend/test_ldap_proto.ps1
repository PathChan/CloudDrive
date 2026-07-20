$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName System.DirectoryServices.Protocols

$hostName   = 'dccntj002.corp.novocorp.net'
$bindUser   = 'CORP\stjvmssa'
$bindPass   = 'dNF,dzm9n+X.dC76zQSzh,gXwM,Jw9Wg'
$searchBase = 'OU=UserAccounts,OU=CNTJ,OU=Company,DC=corp,DC=novocorp,DC=net'
$userFilter = "(&(objectClass=user)(sAMAccountName=bwkk))"

Write-Host "=== LdapConnection test ===" -ForegroundColor Cyan
try {
    $server = New-Object System.DirectoryServices.Protocols.LdapDirectoryIdentifier($hostName, 389, $false, $false)
    $conn = New-Object System.DirectoryServices.Protocols.LdapConnection($server)
    $conn.AuthType = [System.DirectoryServices.Protocols.AuthType]::Ntlm
    $cred = New-Object System.Net.NetworkCredential('stjvmssa', $bindPass, 'CORP')
    $conn.Credential = $cred
    $conn.SessionOptions.ReferralChasing = [System.DirectoryServices.Protocols.ReferralChasingOptions]::None
    $conn.Timeout = [TimeSpan]::FromSeconds(10)
    
    Write-Host "  Binding..." -ForegroundColor Gray
    $conn.Bind()
    Write-Host "  [OK] Bind succeeded" -ForegroundColor Green
    
    Write-Host "  Searching..." -ForegroundColor Gray
    $req = New-Object System.DirectoryServices.Protocols.SearchRequest(
        $searchBase,
        $userFilter,
        [System.DirectoryServices.Protocols.SearchScope]::Subtree,
        @('mail','displayName','userPrincipalName','distinguishedName','memberOf')
    )
    $res = $conn.SendRequest($req)
    
    if ($res -is [System.DirectoryServices.Protocols.SearchResponse]) {
        if ($res.Entries.Count -gt 0) {
            $entry = $res.Entries[0]
            Write-Host "  [OK] Found:" -ForegroundColor Green
            Write-Host "    DN: $($entry.DistinguishedName)"
            
            $mail = ''
            if ($entry.Attributes['mail']) { $mail = $entry.Attributes['mail'][0] }
            Write-Host "    mail: $mail"
            
            $dn = $entry.DistinguishedName.ToString()
            
            # Verify password
            Write-Host "  Verifying password..." -ForegroundColor Gray
            $server2 = New-Object System.DirectoryServices.Protocols.LdapDirectoryIdentifier($hostName, 389, $false, $false)
            $conn2 = New-Object System.DirectoryServices.Protocols.LdapConnection($server2)
            $conn2.AuthType = [System.DirectoryServices.Protocols.AuthType]::Ntlm
            $cred2 = New-Object System.Net.NetworkCredential('bwkk', '111111', 'CORP')
            $conn2.Credential = $cred2
            $conn2.Bind()
            Write-Host "  [OK] Password correct!" -ForegroundColor Green
        } else {
            Write-Host "  [OK] No entries found" -ForegroundColor Yellow
        }
    } else {
        Write-Host "  Unexpected response type: $($res.GetType())"
    }
} catch {
    Write-Host "  [FAIL] $($_.Exception.GetType().Name): $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  Stack: $($_.ScriptStackTrace)" -ForegroundColor Gray
}
