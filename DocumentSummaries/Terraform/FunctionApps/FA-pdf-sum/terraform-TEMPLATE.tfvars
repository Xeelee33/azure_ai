#Top Level Subscription Variables/Universal Variables
tenant_id = ""
core_subscription_id = ""
location = "usgovvirginia"
resource_group_ampls = ""
ampls_name = ""
storage_pe_list = ["blob", "queue", "file", "table", "dfs"]


#AI Subscription Variables
subscription_id = ""
resource_group_name_ai = ""
resource_group_name_net = ""
resource_group_name_misc = ""
vnet_name_internal = ""
subnet_name_internal = ""
vnet_name_external = ""
subnet_name_external = ""
key_vault_name = ""
log_analytics_workspace_name = ""
data_lake_storage = ""
tags = {
    managedBy = "terraform"
    environment = "development"
}

# Function App Specific Variables
function_app_name = "FA-pdf-sum"
app_service_plan_prefix = "ASP-Linux"
storage_container_name = "raw"

# Cosmos DB Variables - Development
## Free Cosmos Instance
# cosmos_db_name = ""
# cosmos_db_database = ""
# cosmos_db_container = ""
## Paid Cosmos Instance
cosmos_db_name = ""
cosmos_db_database = ""
cosmos_db_container = ""

# Below settings should be true during initial deployment and then set to false in post-deployment
# This controls whether services can be accessed over public internet or must be forced through private links/endpoints
public_access_enabled = true