# Lab 2 - Identity, Azure integration and live secret rotation

**Level:** intermediate+. **Scenario:** security rejects connection strings and a shared application identity. Demonstrate separately authorized API/worker access and explain what actually happens during token exchange.

**Prerequisite:** lab 1 healthy, no Flux reconciliation yet. Keep the private management connection and API port-forward. All resource names come from `. .\scripts\Use-Lab.ps1`. Additional cost is limited to synthetic traffic and secret operations on existing resources.

## Directives

### 1. Map identity to permission before touching a token

```powershell
. .\scripts\Use-Lab.ps1
az identity federated-credential list -g $Lab.ResourceGroup --identity-name "$($Lab.Prefix)-api" -o json
az identity federated-credential list -g $Lab.ResourceGroup --identity-name "$($Lab.Prefix)-worker" -o json
az role assignment list --assignee $Outputs.apiPrincipalId.value --all -o table
az role assignment list --assignee $Outputs.workerPrincipalId.value --all -o table
kubectl get serviceaccounts -n orders -o yaml
```

Verify issuer equals the cluster OIDC issuer, audience is `api://AzureADTokenExchange`, and subjects exactly match `system:serviceaccount:orders:order-api` / `order-worker`. API has Service Bus Data Sender; worker has Data Receiver at queue scope. Only API has Key Vault Secrets User.

Do not print projected tokens. Inspect only the injected environment variable names and projected volume configuration:

```powershell
kubectl get deployment order-api -n orders -o yaml
$pod = kubectl get pod -n orders -l app=order-api -o jsonpath='{.items[0].metadata.name}'
kubectl get pod $pod -n orders -o jsonpath='{.spec.containers[0].env}'
```

The admission webhook acts on **pods**, so look at the actual pod for injected Azure values. `automountServiceAccountToken: false` avoids the default Kubernetes API token; the Workload ID webhook injects its separately scoped federation token.

### 2. Submit and observe an asynchronous order

```powershell
$order = @{ id = "identity-$([guid]::NewGuid().ToString('N'))"; item = 'synthetic-widget' }
Invoke-RestMethod http://localhost:8080/orders -Method Post `
  -ContentType application/json -Body ($order | ConvertTo-Json)
kubectl logs -n orders deployment/order-worker --since=5m
az servicebus queue show -g $Lab.ResourceGroup --namespace-name $Lab.ServiceBusName `
  --name orders --query countDetails -o json
```

Expected: HTTP 202 and a worker `order_processed` log with the same ID. Counts can already be zero if consumption is fast. A 202 response alone is not evidence of completed business processing.

### 3. Prove the worker cannot read Key Vault

Grant your human identity temporary **Key Vault Secrets Officer** at this vault to create a synthetic value; do not give that role to the app:

```powershell
$me = az ad signed-in-user show --query id -o tsv
az role assignment create --assignee-object-id $me --assignee-principal-type User `
  --role 'Key Vault Secrets Officer' --scope $Outputs.keyVaultId.value
az keyvault secret set --vault-name $Lab.KeyVaultName --name lab-version --value version-one -o none
$workerPod = kubectl get pods -n orders -l app=order-worker -o jsonpath='{.items[0].metadata.name}'
$check = "from azure.identity import DefaultAzureCredential; from azure.keyvault.secrets import SecretClient; SecretClient('https://$($Lab.KeyVaultName).vault.azure.net', DefaultAzureCredential()).get_secret('lab-version')"
```

Run the following expected-failure command separately, then inspect the output:

```powershell
kubectl exec -n orders $workerPod -- python -c $check
```

Expected: **403 Forbidden**, not successful secret access. `Use-Lab.ps1` makes failed native commands terminate that command invocation; run the next section separately. A token acquisition error indicates federation/authentication trouble, while a network timeout is not proof of an RBAC denial.

### 4. Mount a synthetic value using CSI and persist the configuration

```powershell
$provider = Get-Content .\k8s\identity\secret-provider.yaml -Raw
$provider = $provider.Replace('__API_CLIENT_ID__', $Outputs.apiClientId.value).
  Replace('__KEY_VAULT__', $Lab.KeyVaultName).Replace('__TENANT_ID__', $Lab.TenantId)
Set-Content .\rendered\base\secret-provider.yaml $provider -Encoding utf8
Copy-Item .\k8s\identity\mount-patch.yaml .\rendered\base\identity-patch.yaml
$k = Get-Content .\rendered\base\kustomization.yaml -Raw
if ($k -notmatch 'secret-provider.yaml') {
  $k = $k.Replace('  - worker.yaml', "  - worker.yaml`n  - secret-provider.yaml")
  $k += "`npatches:`n  - path: identity-patch.yaml`n"
  Set-Content .\rendered\base\kustomization.yaml $k -Encoding utf8
}
kubectl apply -k .\rendered\base
kubectl rollout status deployment/order-api -n orders --timeout=300s
kubectl get secretproviderclasspodstatus -n orders
```

Restart the port-forward if its pod was replaced. `GET /config-version` should show `version-one`:

```powershell
Invoke-RestMethod http://localhost:8080/config-version
az keyvault secret set --vault-name $Lab.KeyVaultName --name lab-version --value version-two -o none
```

Poll within a bounded interval:

```powershell
$deadline = (Get-Date).AddMinutes(6)
do {
  $config = Invoke-RestMethod http://localhost:8080/config-version
  if ($config.lab_version -eq 'version-two') { break }
  Start-Sleep -Seconds 10
} while ((Get-Date) -lt $deadline)
if ($config.lab_version -ne 'version-two') { throw 'CSI rotation did not reach the application; inspect mount status and provider logs.' }
```

The app rereads the mounted file on every call; environment variables would not update automatically. This example intentionally does not sync to a Kubernetes Secret. CSI does not itself force every application to reload configuration.

### 5. Break the federation subject and recover

Temporarily point the API federation to a nonexistent service account:

```powershell
az identity federated-credential update -g $Lab.ResourceGroup `
  --identity-name "$($Lab.Prefix)-api" --name orders-api `
  --subject system:serviceaccount:orders:missing-api
kubectl rollout restart deployment/order-api -n orders
kubectl get pods -n orders
kubectl get events -n orders --sort-by=.lastTimestamp
```

Expected: a new pod cannot acquire the federated identity for its CSI mount; existing pods can continue on cached tokens. Diagnose issuer/subject/audience and token errors rather than opening network access or increasing RBAC privilege.

**Solution:**

```powershell
az identity federated-credential update -g $Lab.ResourceGroup `
  --identity-name "$($Lab.Prefix)-api" --name orders-api `
  --subject system:serviceaccount:orders:order-api
kubectl rollout restart deployment/order-api -n orders
kubectl rollout status deployment/order-api -n orders --timeout=300s
```

Allow federation propagation before interpreting a repeated mount failure. Restart the port-forward and submit another synthetic order to verify the API as well as the CSI mount.

## Exit evidence and customer discussion

Keep evidence of sender/receiver separation, a worker Key Vault 403, CSI rotation without pod restart, and federation failure/recovery. Do not save tokens or real secrets as evidence.

| Customer question | Model answer |
|---|---|
| Does Workload ID remove all secrets? | It replaces credentials for supported token-based integrations. Certificates, third-party API keys and other application configuration still need lifecycle management. |
| Why separate API and worker identities? | A compromised API should not gain queue consumption or unrelated secret access; permissions follow the specific component. |
| Why is a correct Azure role not enough? | Federation must first issue a token; network reachability and data-plane authorization are additional independent requirements. |
| Can every team annotate a pod with this client ID? | The federated subject and issuer restrict which service account can exchange a token. Kubernetes permissions to create/use pods in that namespace remain security-critical. |
| Is in-memory duplicate detection reliable? | Only inside one current worker process. Restart-safe idempotency is introduced with durable storage in lab 8. |

## Cleanup and references

Restore the federation subject, leave CSI resources mounted, and retain `version-two` for later labs. Keep the extended `rendered\base` directory: lab 4 adopts it into Flux. **Do not rerun the base renderer after extending it**, because its initial kustomization would replace these additions. Remove the human Secrets Officer role at final cleanup or when a governed operator takes over.

Sources reviewed 2026-09-10: [Workload ID overview](https://learn.microsoft.com/azure/aks/workload-identity-overview), [CSI and identity](https://learn.microsoft.com/azure/aks/csi-secrets-store-identity-access), [CSI rotation](https://learn.microsoft.com/azure/aks/csi-secrets-store-configuration-options), [Service Bus Entra authorization](https://learn.microsoft.com/azure/service-bus-messaging/authenticate-application).
