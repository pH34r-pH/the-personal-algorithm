targetScope = 'resourceGroup'
param location string = resourceGroup().location
param namePrefix string
param image string
@secure()
param ownerSubject string
param githubClientId string
param githubClientSecretName string = 'github-client-secret'
param targetPort int = 8000

var storageName = take(replace('${namePrefix}state', '-', ''), 24)
var vaultName = take('${namePrefix}-kv', 24)
var envName = '${namePrefix}-env'
var appName = '${namePrefix}-app'

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-logs'
  location: location
  properties: { retentionInDays: 30 }
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: { allowBlobPublicAccess: false, minimumTlsVersion: 'TLS1_2', supportsHttpsTrafficOnly: true }
}
resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource share 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: 'tpa-state'
  properties: { shareQuota: 5 }
}
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: vaultName
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enablePurgeProtection: true
    softDeleteRetentionInDays: 90
    publicNetworkAccess: 'Enabled'
  }
}
resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: envName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: { customerId: logs.properties.customerId, sharedKey: logs.listKeys().primarySharedKey }
    }
  }
}
resource storageLink 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: environment
  name: 'state'
  properties: { azureFile: { accountName: storage.name, accountKey: storage.listKeys().keys[0].value, shareName: 'tpa-state', accessMode: 'ReadWrite' } }
}
resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, allowInsecure: false, targetPort: targetPort, transport: 'auto' }
      secrets: [
        { name: 'github-client-secret', keyVaultUrl: '${vault.properties.vaultUri}secrets/${githubClientSecretName}', identity: 'system' }
      ]
    }
    template: {
      containers: [
        {
          name: 'personal-algorithm'
          image: image
          env: [
            { name: 'TPA_DATABASE', value: '/data/personal-algorithm.sqlite3' }
            { name: 'TPA_OWNER_SUBJECT', value: ownerSubject }
            { name: 'TPA_KEY_VAULT_URL', value: vault.properties.vaultUri }
            { name: 'TPA_GITHUB_CLIENT_ID', value: githubClientId }
            { name: 'TPA_GITHUB_CLIENT_SECRET', secretRef: 'github-client-secret' }
          ]
          resources: { cpu: json('0.5'), memory: '1Gi' }
          volumeMounts: [{ volumeName: 'state', mountPath: '/data' }]
        }
      ]
      volumes: [{ name: 'state', storageType: 'AzureFile', storageName: 'state' }]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}
resource kvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, app.id, 'key-vault-secrets-officer')
  scope: vault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
output containerAppName string = app.name
output containerAppFqdn string = app.properties.configuration.ingress.fqdn
output keyVaultName string = vault.name
output storageAccountName string = storage.name
