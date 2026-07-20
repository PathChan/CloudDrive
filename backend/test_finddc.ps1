$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName System.DirectoryServices

$bindUser = 'stjvmssa@corp.novocorp.net'
$bindPass = 'dNF,dzm9n+X.dC76zQSzh,gXwM,Jw9Wg'

Write-Host "=== Find DC with explicit credentials ===" -ForegroundColor Cyan
try {
    $context = New-Object System.DirectoryServices.ActiveDirectory.DirectoryContext(
        [System.DirectoryServices.ActiveDirectory.DirectoryContextType]::Domain,
        'corp.novocorp.net',
        $bindUser,
        $bindPass
    )
    $dc = [System.DirectoryServices.ActiveDirectory.DomainController]::FindOne($context)
    Write-Host "[OK] DC found:" -ForegroundColor Green
    Write-Host "  Name: $($dc.Name)"
    Write-Host "  IP: $($dc.IPAddress)"
    Write-Host "  OSVersion: $($dc.OSVersion)"
} catch {
    Write-Host "[FAIL] $($_.Exception.GetType().Name): $($_.Exception.Message)" -ForegroundColor Red
}
