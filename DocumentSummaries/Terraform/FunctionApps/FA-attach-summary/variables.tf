variable "core_subscription_id" {
  description = "Subscription ID for the OIG's CORE subscription"
  type = string
}

variable "resource_group_ampls" {
  description = "The Resource Group name of the Azure Monitor Private Link Scope (AMPLS) instance in CORE"
  type = string
}

variable "ampls_name" {
  description = "Resource Name of the AMPLS instance in CORE"
  type = string
}

variable "resource_group_name_ai" {
  description = "The name of the ai resource group"
  type        = string
}

variable "resource_group_name_net" {
  description = "The name of the networking resource group"
  type        = string
}

variable "resource_group_name_misc" {
  description = "The name of the misc resource group"
  type        = string
}

# variable "resource_group_name_storage" {
#   description = "The name of the storage account resource group"
#   type        = string
# }

variable "location" {
  description = "The location of the resources"
  type        = string
}

variable "vnet_name_internal" {
  description = "The name of the internal facing virtual network"
  type        = string
}

variable "vnet_name_external" {
  description = "The name of the external facing virtual network"
  type        = string
}

variable "subnet_name_internal" {
  description = "The name of the internal facing subnet"
  type        = string
}

variable "subnet_name_external" {
  description = "The name of the external facing subnet"
  type        = string
}

# variable "subnet_address_prefixes" {
#   description = "The address prefixes of the subnet"
#   type        = list(string)
# }

variable "app_service_plan_prefix" {
  description = "The prefix of the App Service Plan nam"
  type        = string
}

variable "function_app_name" {
  description = "The name of the Function App"
  type        = string
}

variable "storage_pe_list" {
  description = "List of private endpoints types to create for each storage account"
  type = list(string)
}

variable "key_vault_name" {
  description = "The name of the KeyVault"
  type = string
}

variable "tenant_id" {
  description = "The ID of the tenant"
  type = string
}

variable "data_lake_storage" {
  description = "The name of the data lake gen2 storage account the function app triggers on"
  type = string
}


variable "subscription_id" {
  description = "The subscription ID for the target Azure subscription"
  type = string
}

variable "log_analytics_workspace_name" {
  description = "Name of the Log Analytics workspace associated with the Application Insights instance"
  type = string
}

variable "tags" {
  description = "Common tags for resources"
  type = map
}

variable "public_access_enabled" {
  description = "True if public access is enabled, False if only private access"
  type = bool
}

variable "cosmos_db_database" {
  description = "Database name of the Cosmos DB instance used by the function app"
  type = string
}

variable "cosmos_db_name" {
  description = "Name of the cosmos db resource"
  type = string
}

variable "cosmos_db_container" {
  description = "Container name of the Cosmos DB database used by the function app"
  type = string
}

variable "storage_container_name" {
  description = "value of the blob container name used by the blob client to save email attachments"
  type = string
}