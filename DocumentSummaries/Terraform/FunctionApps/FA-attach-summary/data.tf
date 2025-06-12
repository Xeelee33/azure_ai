# Define the data sources called by the resources and outputs
data "azurerm_subscription" "primary" {
}

data "azurerm_resource_group" "rgai" {
  name = var.resource_group_name_ai
}

data "azurerm_resource_group" "rgnet" {
  name = var.resource_group_name_net
}

data "azurerm_subnet" "subnet_internal" {
  name = var.subnet_name_internal
  resource_group_name = var.resource_group_name_net
  virtual_network_name = var.vnet_name_internal
}

data "azurerm_subnet" "subnet_external" {
  name = var.subnet_name_external
  resource_group_name = var.resource_group_name_net
  virtual_network_name = var.vnet_name_external
}

data "azurerm_key_vault" "keyvault" {
  resource_group_name = var.resource_group_name_misc
  name = var.key_vault_name
}

data "azurerm_role_definition" "key_vault_secrets_user" {
  name = "Key Vault Secrets User"
  scope  = data.azurerm_subscription.primary.id
}

data "azurerm_storage_account" "data_lake_01" {
  name = var.data_lake_storage
  resource_group_name = var.resource_group_name_ai
}

data "azurerm_log_analytics_workspace" "log_analytics" {
  name                = var.log_analytics_workspace_name
  resource_group_name = var.resource_group_name_misc

}

