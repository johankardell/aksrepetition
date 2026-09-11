# Lab 2 - Identity, Azure integration and live secret rotation

**Level:** intermediate+. **Scenario:** security rejects connection strings and a shared application identity. Demonstrate separately authorized API/worker access and explain what actually happens during token exchange.

**Prerequisite:** lab 1 healthy, no Flux reconciliation yet. Keep the private management connection and API port-forward. All resource names come from `. .\scripts\Use-Lab.ps1`. Additional cost is limited to synthetic traffic and secret operations on existing resources.

## Directives

### 1. Map identity to permission before touching a token

**Task:** map each service account to its federated identity and narrowly scoped Azure roles. Explain the token exchange without printing or saving projected tokens.

<details>
<summary>Solution</summary>

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

The SDK reads the injected client/tenant IDs and projected token file. Entra validates that the token's issuer, subject and audience match the federated credential, then issues an access token for the requested Azure service. That service independently checks the identity's data-plane role and scope. A valid federation does not grant every Azure permission, and an Azure role does not repair a mismatched federation.

</details>

### 2. Submit and observe an asynchronous order

**Task:** submit one uniquely identified synthetic order and correlate acceptance with worker processing. Explain why a successful HTTP response or an empty queue alone is insufficient evidence of completion.

<details>
<summary>Solution</summary>

```powershell
$order = @{ id = "identity-$([guid]::NewGuid().ToString('N'))"; item = 'synthetic-widget' }
Invoke-RestMethod http://localhost:8080/orders -Method Post `
  -ContentType application/json -Body ($order | ConvertTo-Json)
kubectl logs -n orders deployment/order-worker --since=5m
az servicebus queue show -g $Lab.ResourceGroup --namespace-name $Lab.ServiceBusName `
  --name orders --query countDetails -o json
```

Expected: HTTP 202 and a worker `order_processed` log with the same ID. Counts can already be zero if consumption is fast. A 202 response alone is not evidence of completed business processing.

</details>

### 3. Prove the worker cannot read Key Vault

**Task:** create a synthetic vault value as a separately authorized human and attempt to read it as the worker. Distinguish authorization denial from authentication or network failure. Never grant the worker a vault role to make this negative test pass.

<details>
<summary>Solution</summary>

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

</details>

### 4. Mount a synthetic value using CSI and persist the configuration

**Task:** mount `lab-version` in the API using CSI, preserve the rendered configuration for later GitOps adoption, and demonstrate a bounded live rotation without restarting the pod. Use synthetic values only; do not rerun the base renderer after extending it.

<details>
<summary>Solution</summary>

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

Restart the port-forward if its pod was replaced. `GET /config-version` should show `version-one`. Record the API pod UIDs and restart counts before rotating the value:

```powershell
Invoke-RestMethod http://localhost:8080/config-version
kubectl get pods -n orders -l app=order-api `
  -o custom-columns='NAME:.metadata.name,UID:.metadata.uid,RESTARTS:.status.containerStatuses[*].restartCount'
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

After observing `version-two`, compare the API pod UIDs and restart counts with the saved before-state:

```powershell
kubectl get pods -n orders -l app=order-api `
  -o custom-columns='NAME:.metadata.name,UID:.metadata.uid,RESTARTS:.status.containerStatuses[*].restartCount'
```

Unchanged UID/restart counts alongside the two observed values distinguish live rotation from configuration taking effect only in a replacement pod.

</details>

### 5. Break the federation subject and recover

**Task:** change only the API's federated subject in this lab, observe the resulting failure, and recover with a new successfully processed order. Restore federation before pausing; do not widen network access or Azure roles.

<details>
<summary>Solution</summary>

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

**Recovery:**

```powershell
az identity federated-credential update -g $Lab.ResourceGroup `
  --identity-name "$($Lab.Prefix)-api" --name orders-api `
  --subject system:serviceaccount:orders:order-api
kubectl rollout restart deployment/order-api -n orders
kubectl rollout status deployment/order-api -n orders --timeout=300s
```

Allow federation propagation before interpreting a repeated mount failure. Restart the port-forward and submit another synthetic order to verify the API as well as the CSI mount.

</details>

## Exit evidence and customer discussion

Keep evidence of sender/receiver separation, a worker Key Vault 403, CSI rotation without pod restart, and federation failure/recovery. Do not save tokens or real secrets as evidence.

<details>
<summary>Model answer: Does Workload ID remove all secrets?</summary>

It replaces credentials for supported token-based integrations. Certificates, third-party API keys and other application configuration still need lifecycle management.

</details>

<details>
<summary>Model answer: Why separate API and worker identities?</summary>

A compromised API should not gain queue consumption or unrelated secret access; permissions follow the specific component.

</details>

<details>
<summary>Model answer: Why is a correct Azure role not enough?</summary>

Federation must first issue a token; network reachability and data-plane authorization are additional independent requirements.

</details>

<details>
<summary>Model answer: Can every team annotate a pod with this client ID?</summary>

The federated subject and issuer restrict which service account can exchange a token. Kubernetes permissions to create/use pods in that namespace remain security-critical.

</details>

<details>
<summary>Model answer: Is in-memory duplicate detection reliable?</summary>

Only inside one current worker process. Restart-safe idempotency is introduced with durable storage in lab 8.

</details>

## Cleanup and references

Restore the federation subject, leave CSI resources mounted, and retain `version-two` for later labs. Keep the extended `rendered\base` directory: lab 4 adopts it into Flux. **Do not rerun the base renderer after extending it**, because its initial kustomization would replace these additions. Remove the human Secrets Officer role at final cleanup or when a governed operator takes over.

<details>
<summary>Solution: cumulative cleanup</summary>

Use task 5's recovery and confirm the API subject is `system:serviceaccount:orders:order-api` with task 1's federated-credential query. Through the restored port-forward, confirm `/config-version` still reports `version-two` and submit a fresh synthetic order using task 2. Do not delete the SecretProviderClass or mount patch.

When the temporary human role is no longer needed, list the assignments at the vault and identify the exact assignment created for this exercise:

```powershell
$me = az ad signed-in-user show --query id -o tsv
az role assignment list --assignee $me --scope $Outputs.keyVaultId.value `
  --role 'Key Vault Secrets Officer' --query '[].{id:id,principal:principalId,scope:scope}' -o table
```

After confirming ownership and scope, remove only that assignment by its displayed ID with `az role assignment delete --ids <assignment-id>`. Do not remove pre-existing access or either workload identity's roles; if the role predates this lab, leave it to the authorization owner.

</details>

Sources reviewed 2026-09-10: [Workload ID overview](https://learn.microsoft.com/azure/aks/workload-identity-overview), [CSI and identity](https://learn.microsoft.com/azure/aks/csi-secrets-store-identity-access), [CSI rotation](https://learn.microsoft.com/azure/aks/csi-secrets-store-configuration-options), [Service Bus Entra authorization](https://learn.microsoft.com/azure/service-bus-messaging/authenticate-application).
