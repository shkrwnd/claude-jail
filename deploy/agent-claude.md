# Claude Code — Authorization Rules

Every command you run goes through an authorization layer.
You MUST set `AGENT_REASON` before every shell command explaining WHY you are running it.

## Required format

```bash
AGENT_REASON="<clear explanation of why you are running this command>" <command>
```

## Examples

```bash
AGENT_REASON="listing Azure subscriptions to identify the active account" az account list

AGENT_REASON="checking git log to understand recent changes before making edits" git log --oneline -10

AGENT_REASON="listing S3 buckets to find where the deployment artifacts are stored" aws s3 ls
```

## Rules

- Always explain the intent, not just what the command does
- Be specific — "checking deployment status" is better than "running kubectl"
- Never leave AGENT_REASON empty
- If a command returns `PENDING APPROVAL`, a human has not approved it yet:
  tell the user an approval is waiting, then retry the same command once they
  have granted it
- If a command returns `DENIED`, explain the denial to the user — do NOT
  retry variations of the command to get around the policy
- Commands are authorized and executed externally — you only receive the output
