# Generate PowerShell commands to create the DNS entry for each Function App and Storage Account Private Endpoint created and output by Terraform
# Reference for private endpoint DNS zones: https://learn.microsoft.com/en-us/azure/private-link/private-endpoint-dns#government
$private_endpoints = $(terraform output -json | ConvertFrom-JSON).psobject.properties.Value.value
$zone_hash = @{
      ".azurewebsites.us"="privatelink.azurewebsites.us"
      ".scm.azurewebsites.us"="privatelink.azurewebsites.us" 
      ".blob.core.usgovcloudapi.net"="privatelink.blob.core.usgovcloudapi.net" 
      ".queue.core.usgovcloudapi.net"="privatelink.queue.core.usgovcloudapi.net"
      ".file.core.usgovcloudapi.net"="privatelink.file.core.usgovcloudapi.net" 
      ".table.core.usgovcloudapi.net"="privatelink.table.core.usgovcloudapi.net"
      ".dfs.core.usgovcloudapi.net"="privatelink.dfs.core.usgovcloudapi.net"  
}

Write-Output "###Run below commands in Domain Admin PowerShell session to add Private Endpoint DNS entries"
foreach($endpoint in $private_endpoints) {
    #Write-Output "-ZoneName $($endpoint.fqdn) -IPv4Address $($endpoint.ip_addresses)"
    $name = $($endpoint.fqdn).Split(".")[0]
    $private_zone = $endpoint.fqdn -replace $name, ""
    if($($endpoint.fqdn).Contains(".scm.")){
        $name = "$name.SCM"
    }
    if ($zone_hash.ContainsKey($private_zone)) {
        Write-Output "Add-DnsServerResourceRecordA -ComputerName `"<domain controller hostname>`" -ZoneName `"$($zone_hash[$private_zone])`" -Name `"$name`" -IPv4Address `"$($endpoint.ip_addresses)`""
    }
    
}

Write-Output "###Add each of the entries below to Zscalar ZPA Application Segment 'OIG AI DEV'"
foreach($endpoint in $private_endpoints) {
    Write-Output $endpoint.fqdn
}
