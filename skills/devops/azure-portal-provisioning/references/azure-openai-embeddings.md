# Foundry / Azure OpenAI resource for an embedding model (Entra-only)

Use for a small embedding endpoint (e.g. agent memory vectors). Both "Create a Foundry resource" and the classic "Azure OpenAI" create path work; Foundry resources expose the same `https://<name>.openai.azure.com` endpoint.

## Before creating
- If the model "isn't there", check the live retirement schedule before assuming it is gone: https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirement-schedule (the older `ai-foundry/...` path 404s). Models are listed only inside a created resource (Foundry portal > Models + endpoints), not on the Foundry landing page or the Azure resource blade.
- CLI check once the resource exists: `az cognitiveservices account list-models -g <rg> -n <name> --query "[?contains(name,'embedding')].name" -o tsv`. Empty = region/subscription doesn't offer it; recreate in East US / East US 2 / Sweden Central.

## Wizard tabs
- Basics: dedicated RG, name `oai-<purpose>`, region with the model, Standard S0. A Foundry resource also creates a default project; harmless.
- Storage / Key Vault / App Insights: leave empty; only agent/app features use them.
- Inbound networking: do NOT create the proposed VNet/subnet for a laptop client (VNets are for Azure-to-Azure private traffic). Prefer Selected networks + the office egress IP; if the wizard insists on a VNet, choose All networks and restrict after creation (parent resource > Networking > Selected networks > add IP > Save). Entra + RBAC still gate access meanwhile.
- Identity: System assigned if a choice is forced (unused here).

## Deploy the model
Foundry portal > Models + endpoints > + Deploy model > Deploy base model > search the model > Confirm. Name the deployment exactly as the client code expects. Deployment type decides where prompts are processed: Global Standard = any region worldwide; Data Zone Standard (US) or Standard = stays in-geo. For employer data choose Data Zone Standard, else Standard. CLI alternative: `az cognitiveservices account deployment create ... --sku-name Standard --sku-capacity 10`.

## Endpoint
Use the base `https://<name>.openai.azure.com` (drop `/openai/v1` shown on the Foundry home page) when the client builds `/openai/deployments/<dep>/embeddings?api-version=...` itself. Never copy the API key; Entra only. Optional hardening: `az resource update --ids $id --set properties.disableLocalAuth=true`.

## Access (data plane)
```powershell
$oaiId = az cognitiveservices account show -g <rg> -n <name> --query id -o tsv
az role assignment create --assignee <upn> --role "Cognitive Services OpenAI User" --scope $oaiId
```
The wizard's "Foundry User" grant does not include the embeddings data action. MFA errors on this command: see SKILL.md rule (use portal IAM).

## Smoke test (expected `1536` for text-embedding-3-small)
```powershell
$ep  = "https://<name>.openai.azure.com"
$tok = az account get-access-token --resource https://cognitiveservices.azure.com --query accessToken -o tsv
$r = Invoke-RestMethod -Method Post -Uri "$ep/openai/deployments/text-embedding-3-small/embeddings?api-version=2024-02-01" -Headers @{Authorization="Bearer $tok"} -ContentType "application/json" -Body '{"input":"test"}'
$r.data[0].embedding.Count
```
- PermissionDenied / lacks data action, 401/403: role missing or not yet propagated; list assignments, assign, wait, fresh token.
- 404: deployment name mismatch or not deployed.
- Connection/firewall error: IP rule vs current network.
- "Cannot index into a null array": only a consequence of the failed call above it.
