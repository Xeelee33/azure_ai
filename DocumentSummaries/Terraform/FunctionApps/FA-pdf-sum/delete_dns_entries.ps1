# References: 
# https://rcmtech.wordpress.com/2014/02/26/get-and-delete-dns-a-and-ptr-records-via-powershell/
# https://learn.microsoft.com/en-us/powershell/module/dnsserver/remove-dnsserverresourcerecord?view=windowsserver2019-ps

# To Remove DNS Records

# Set the Storage Account Name and Function App Name to the names of the resources you want to delete DNS entries for
$StorageAccountName = ""
$FunctionAppName = ""


$StorageZoneName = "privatelink.blob.core.usgovcloudapi.net", 
    "privatelink.queue.core.usgovcloudapi.net",
    "privatelink.file.core.usgovcloudapi.net", 
    "privatelink.table.core.usgovcloudapi.net",
    "privatelink.dfs.core.usgovcloudapi.net"
$FunctionAppZoneName = "privatelink.azurewebsites.us" 
$DNSServer = "<domain controller hostname>"
### DELETE STORAGE ACCCOUNT DNS ENTRIES
foreach($ZoneName in $StorageZoneName) {
    $NodeDNS = $null
    $NodeDNS = Get-DnsServerResourceRecord -ZoneName $ZoneName -ComputerName $DNSServer -Node $StorageAccountName -RRType A -ErrorAction SilentlyContinue
    if($null -eq $NodeDNS){
        Write-Host "No DNS record found for $StorageAccountName.$ZoneName"
    } else {
        Remove-DnsServerResourceRecord -ZoneName $ZoneName -ComputerName $DNSServer -InputObject $NodeDNS -Force
        Write-Output "DNS record for $StorageAccountName.$ZoneName deleted"
    }
}
### DELETE BOTH FUNCTION APP DNS ENTRIES 
# Set the ZoneName to the Function App Private Link Zone
$ZoneName = $FunctionAppZoneName
$NodeDNS = $null
# Delete the base Entry
$NodeDNS = Get-DnsServerResourceRecord -ZoneName $ZoneName -ComputerName $DNSServer -Node $FunctionAppName -RRType A -ErrorAction SilentlyContinue
if($null -eq $NodeDNS){
    Write-Host "No DNS record found for $StorageAccountName.$ZoneName"
} else {
    Remove-DnsServerResourceRecord -ZoneName $ZoneName -ComputerName $DNSServer -InputObject $NodeDNS -Force
    Write-Output "DNS record for $StorageAccountName.$ZoneName deleted"
}
$NodeDNS = $null
# Delete the .SCM Entry
$NodeDNS = Get-DnsServerResourceRecord -ZoneName $ZoneName -ComputerName $DNSServer -Node "$FunctionAppName.SCM" -RRType A -ErrorAction SilentlyContinue
if($null -eq $NodeDNS){
    Write-Host "No DNS record found for $StorageAccountName.$ZoneName"
} else {
    Remove-DnsServerResourceRecord -ZoneName $ZoneName -ComputerName $DNSServer -InputObject $NodeDNS -Force
    Write-Output "DNS record for $StorageAccountName.$ZoneName deleted"
}