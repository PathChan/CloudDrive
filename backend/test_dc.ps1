$ErrorActionPreference = 'Continue'

$hostName   = 'dccntj002.corp.novocorp.net'
$bindUser   = 'CORP\stjvmssa'
$bindPass   = 'dNF,dzm9n+X.dC76zQSzh,gXwM,Jw9Wg'

# Try to get DC info using .NET
Write-Host "=== .NET Domain.GetCurrentDomain ===" -ForegroundColor Cyan
try {
    $domain = [System.DirectoryServices.ActiveDirectory.Domain]::GetCurrentDomain()
    Write-Host "Domain: $($domain.Name)" -ForegroundColor Green
    $dc = $domain.FindDomainController()
    Write-Host "DC: $($dc.Name) -> $($dc.IPAddress)" -ForegroundColor Green
} catch {
    Write-Host "FAIL: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== ADSI bind + get server IP ===" -ForegroundColor Cyan
try {
    $de = New-Object DirectoryServices.DirectoryEntry("LDAP://$hostName", $bindUser, $bindPass)
    $null = $de.NativeObject
    
    # Try to get properties that might contain server info
    Write-Host "Path: $($de.Path)"
    Write-Host "Name: $($de.Name)"
    Write-Host "SchemaClassName: $($de.SchemaClassName)"
    
    # Try to access the ADsObject to get server info
    $native = $de.NativeObject
    Write-Host "NativeObject type: $($native.GetType().FullName)"
    
    # Try dnsHostName
    try { Write-Host "dnsHostName: $($de.Properties['dnsHostName'][0])" } catch {}
    try { Write-Host "serverName: $($de.Properties['serverName'][0])" } catch {}
    try { Write-Host "cn: $($de.Properties['cn'][0])" } catch {}
    
    # Try Options.GetCurrentServerName
    try {
        $options = $de.Options
        $serverName = $options.GetCurrentServerName()
        Write-Host "ServerName from Options: $serverName" -ForegroundColor Green
    } catch {
        Write-Host "Options.GetCurrentServerName failed: $($_.Exception.Message)"
    }
    
} catch {
    Write-Host "FAIL: $($_.Exception.Message)" -ForegroundColor Red
}
