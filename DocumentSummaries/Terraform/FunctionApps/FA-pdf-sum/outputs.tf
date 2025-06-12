# PowerShell command to list fqds and IP addresses: $(terraform output -json | ConvertFrom-JSON).psobject.properties.Value.value
output "pe_sta_fa_dns_configs" {
  description = "The private endpoint dns configs."
  #value       = azurerm_private_endpoint.pe-sta-fa["dfs"].custom_dns_configs
  value = [
    for type in var.storage_pe_list : element(azurerm_private_endpoint.pe_function_app_storage["${type}"].custom_dns_configs, 1)
  ]
}

output "pe_function_app" {
  description = "The private endpoint DNS info of the function app" 
  value = azurerm_private_endpoint.pe_linux_function_app.custom_dns_configs
}

# output "func_app_settings" {
#   description = "The Environmental Variables/App Settings of the Function App"
#   sensitive = true
#   value = data.azurerm_linux_function_app.data_func_app_test.app_settings
# }



