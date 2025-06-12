/*
Created by: Joshua Wilshere
Created on: 3/26/25
Purpose: Provision an Azure Linux Function App and related resources, including private endpoints
Instructions:
  1. Provision resources. If this is the inital creation, make sure the azurerm_linux_function_app's storage_account_name and storage_account_access_key are set
  2. Output the private endpoint FQDNs and IPs to add to OIG DNS and Zscalar ZPA
      a. Execute the generate_dns_entries.ps1 script in the same terminal used to run terraform apply to get the output in a useful format
  3. After initial creation completes, update the azurerm_linux_function_app to use storage_key_vault_secret_id and WEBSITE_CONTENTAZUREFILECONNECTIONSTRING
  4. Change the var.public_access_enabled from "true" to "false" to disable public access after adding private endpoints to DNS
  5. Deploy the locally developed Python code to the Function App (this initializes the blobs service key)
  6. Uncomment the code in the postdeployment.tf file and apply changes to create the event subscription and triggers

*/

# Provider from https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/linux_function_app
terraform {
  required_providers {
    azurerm = {
      source = "hashicorp/azurerm"
      #version = "4.24.0"
    }
  }
}

provider "azurerm" {
  features {
      key_vault {
          purge_soft_delete_on_destroy = true
        }
  }
  subscription_id = var.subscription_id
  tenant_id = var.tenant_id
  environment = "usgovernment"

  
}

provider "azurerm" {
  features {}
  subscription_id = var.core_subscription_id
  tenant_id = var.tenant_id
  environment = "usgovernment"
  alias = "core_sub"
}

resource "azurerm_resource_group" "rg_function_app" {
  name     = "RG-${var.function_app_name}"
  location = var.location
  tags = var.tags
  
}

# Provision the App Service Plan for the Function App to Use
# Commented out in favor of using the previously created on via data source reference
resource "azurerm_service_plan" "asp_linux_elastic" {
  location            = var.location
  name                = "${var.app_service_plan_prefix}-${lower(var.function_app_name)}"
  os_type             = "Linux"
  resource_group_name = azurerm_resource_group.rg_function_app.name
  sku_name            = "EP1"
  tags = var.tags
}

# Provision the storage account for the function app
resource "azurerm_storage_account" "function_app_storage" {
  account_replication_type        = "LRS"
  account_tier                    = "Standard"
  allow_nested_items_to_be_public = false
  is_hns_enabled                  = true
  location                        = var.location
  name                            = "sta${replace(replace(lower(var.function_app_name), "-", ""), "_", "")}"
  public_network_access_enabled   = var.public_access_enabled
  #public_network_access_enabled   = false
  resource_group_name             = azurerm_resource_group.rg_function_app.name
  tags = var.tags
}

# Add the storage account's connection string as a secret to the Key Vault
resource "azurerm_key_vault_secret" "kv_function_app_storage_conn" {
  key_vault_id = data.azurerm_key_vault.keyvault.id
  name         = "CONN-${azurerm_storage_account.function_app_storage.name}"
  tags = merge({
    file-encoding = "utf-8"
  }, var.tags)
  value = azurerm_storage_account.function_app_storage.primary_connection_string
  depends_on = [
    azurerm_storage_account.function_app_storage
  ]
}

# Provision private endpoints for the storage account
resource "azurerm_private_endpoint" "pe_function_app_storage" {
  location = var.location
  resource_group_name = azurerm_resource_group.rg_function_app.name
  subnet_id           = data.azurerm_subnet.subnet_internal.id
  for_each = {for type in var.storage_pe_list : type => type}
  name = "PE-${azurerm_storage_account.function_app_storage.name}-${each.value}"
  private_service_connection {
    name                           = "PE-${azurerm_storage_account.function_app_storage.name}-${each.value}"
    private_connection_resource_id = azurerm_storage_account.function_app_storage.id
    subresource_names              = ["${each.value}"]
    is_manual_connection = false
  }
  tags = var.tags
  depends_on = [
    azurerm_storage_account.function_app_storage
  ] 
}

# Provision Application Insights instance for the Function App
resource "azurerm_application_insights" "apin_function_app" {
  application_type           = "web"
  # Below settings should be true in sandbox and false in OIG AI Dev/Prod
  # This controls whether services can be accessed over public internet or must be forced through private links/endpoints
  internet_ingestion_enabled = var.public_access_enabled
  internet_query_enabled     = var.public_access_enabled  
  # internet_ingestion_enabled = false
  # internet_query_enabled     = false
  location                   = var.location
  name                       = "APIN-${var.function_app_name}"
  resource_group_name        = azurerm_resource_group.rg_function_app.name
  sampling_percentage        = 0
  workspace_id               = data.azurerm_log_analytics_workspace.log_analytics.id
  tags = var.tags
}

# Add Application Insights connection string as a secret to the Key Vault
resource "azurerm_key_vault_secret" "kv_apin_function_app_conn" {
  key_vault_id = data.azurerm_key_vault.keyvault.id
  name         = "CONN-${azurerm_application_insights.apin_function_app.name}"
  value = azurerm_application_insights.apin_function_app.connection_string
  depends_on = [
    azurerm_application_insights.apin_function_app
  ]
}

# Provision the Linux Function App with Python v2 Programming Model
# Note - during initial creation, must use storage_account_name and storage_account_access_key
#       but in post-deployment, use storage_key_vault_secret_id and WEBSITE_CONTENTAZUREFILECONNECTIONSTRING
#       to replace exposed keys and connection strings with Key Vault references
resource "azurerm_linux_function_app" "linux_function_app" {
  app_settings = {
    AI_LANGUAGE_ENDPOINT           = "@Microsoft.KeyVault(SecretUri=${data.azurerm_key_vault.keyvault.vault_uri}secrets/AI-LANGUAGE-ENDPOINT-FREE)"
    AI_LANGUAGE_KEY                = "@Microsoft.KeyVault(SecretUri=${data.azurerm_key_vault.keyvault.vault_uri}secrets/AI-LANGUAGE-KEY-FREE)"
    AZURE_COSMOS_CONTAINER_NAME    = var.cosmos_db_container
    AZURE_COSMOS_DATABASE_NAME     = var.cosmos_db_database
    AzureFunctionsWebHost__hostid  = lower(var.function_app_name)
    FORM_RECOGNIZER_ENDPOINT       = "@Microsoft.KeyVault(SecretUri=${data.azurerm_key_vault.keyvault.vault_uri}secrets/FORM-RECOGNIZER-ENDPOINT-FREE)"
    FORM_RECOGNIZER_KEY            = "@Microsoft.KeyVault(SecretUri=${data.azurerm_key_vault.keyvault.vault_uri}secrets/FORM-RECOGNIZER-KEY-FREE)"
    cosmosdb_CONNECTION            = "@Microsoft.KeyVault(SecretUri=${data.azurerm_key_vault.keyvault.vault_uri}secrets/CONN-${var.cosmos_db_name})"
    datalake_STORAGE               = "@Microsoft.KeyVault(SecretUri=${data.azurerm_key_vault.keyvault.vault_uri}secrets/CONN-${data.azurerm_storage_account.data_lake_01.name})"
    STORAGE_CONTAINER_NAME          = var.storage_container_name
    BUILD_FLAGS                    = "UseExpressBuild"
    ENABLE_ORYX_BUILD              = "true"
    SCM_DO_BUILD_DURING_DEPLOYMENT = "1"
    XDG_CACHE_HOME                 = "/tmp/.cache"
    # In post deployment, uncomment out this attribute and comment out storage_account_name and storage_account_access_key
    WEBSITE_CONTENTAZUREFILECONNECTIONSTRING = "@Microsoft.KeyVault(SecretUri=${data.azurerm_key_vault.keyvault.vault_uri}secrets/${azurerm_key_vault_secret.kv_function_app_storage_conn.name})"
  }
  # In post deployment, uncomment out this attribute and comment out storage_account_name and storage_account_access_key
  storage_key_vault_secret_id              = "${data.azurerm_key_vault.keyvault.vault_uri}secrets/${azurerm_key_vault_secret.kv_function_app_storage_conn.name}"

  ## storage_account_name and storage_account_access_key should be used during initial deployment only
  # storage_account_name = azurerm_storage_account.function_app_storage.name
  # storage_account_access_key = azurerm_storage_account.function_app_storage.primary_access_key

  builtin_logging_enabled                  = false
  client_certificate_mode                  = "Required"
  ftp_publish_basic_authentication_enabled = false
  https_only                               = true
  location                                 = var.location
  name                                     = var.function_app_name
  public_network_access_enabled            = var.public_access_enabled
  resource_group_name                      = azurerm_resource_group.rg_function_app.name
  #service_plan_id                          = data.azurerm_service_plan.linux_elastic_service_plan.id
  service_plan_id = azurerm_service_plan.asp_linux_elastic.id
  tags = merge({
    "hidden-link: /app-insights-resource-id" = azurerm_application_insights.apin_function_app.id
  }, var.tags)
  virtual_network_subnet_id                      = data.azurerm_subnet.subnet_external.id
  webdeploy_publish_basic_authentication_enabled = false
  identity {
    type = "SystemAssigned"
  }
  site_config {
    application_insights_connection_string = "@Microsoft.KeyVault(SecretUri=${data.azurerm_key_vault.keyvault.vault_uri}secrets/${azurerm_key_vault_secret.kv_apin_function_app_conn.name})"
    ftps_state                             = "FtpsOnly"
    vnet_route_all_enabled                 = true
    application_stack {
      python_version = "3.11"
    }
    cors {
      allowed_origins = ["https://portal.azure.us"]
    }
  }
  depends_on = [ azurerm_application_insights.apin_function_app, azurerm_key_vault_secret.kv_function_app_storage_conn,
  azurerm_storage_account.function_app_storage ]
}

# Add the Function App's System Assigned managed identity to the Key Vault's RBAC
resource "azurerm_role_assignment" "role_kv_function_app_id" {
  scope = data.azurerm_key_vault.keyvault.id
  role_definition_id = data.azurerm_role_definition.key_vault_secrets_user.id
  principal_id = azurerm_linux_function_app.linux_function_app.identity[0].principal_id
  depends_on = [ azurerm_linux_function_app.linux_function_app ]
}

# Create the Function App's private endpoints
resource "azurerm_private_endpoint" "pe_linux_function_app" {
  location = var.location
  resource_group_name = azurerm_resource_group.rg_function_app.name
  subnet_id           = data.azurerm_subnet.subnet_internal.id
  name = "PE-${lower(var.function_app_name)}"
  private_service_connection {
    name                           = "PE-${lower(var.function_app_name)}"
    private_connection_resource_id = azurerm_linux_function_app.linux_function_app.id
    subresource_names              = ["sites"]
    is_manual_connection = false
  }
  tags = var.tags
  depends_on = [
    azurerm_linux_function_app.linux_function_app
  ] 
}

resource "azurerm_monitor_private_link_scoped_service" "ampls_app_insights_link" {
  name                = "ampls-link-${lower(azurerm_application_insights.apin_function_app.name)}"
  resource_group_name = var.resource_group_ampls
  scope_name          = var.ampls_name
  linked_resource_id  = azurerm_application_insights.apin_function_app.id
  provider = azurerm.core_sub
  depends_on = [azurerm_application_insights.apin_function_app]
}
