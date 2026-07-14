"""Fine-tune the agent's gpt-5-mini deployment on corrected extractions, using Azure
OpenAI's native fine-tuning (via the Foundry project's OpenAI client) instead of a
custom training loop — the job and resulting model show up in the Foundry project's
Fine-tuning tab automatically.

Usage:
  python ml/prepare_training_data.py corrections/ training_data.jsonl
  python ml/trigger_finetune.py training_data.jsonl
"""
import sys
import time
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config


def main(training_file_path: str) -> None:
    project = AIProjectClient(endpoint=config.FOUNDRY_PROJECT_ENDPOINT, credential=DefaultAzureCredential())
    openai_client = project.get_openai_client()

    with open(training_file_path, "rb") as f:
        uploaded = openai_client.files.create(file=f, purpose="fine-tune")

    job = openai_client.fine_tuning.jobs.create(training_file=uploaded.id, model=config.AGENT_MODEL_DEPLOYMENT)
    print(f"Submitted fine-tuning job {job.id}, status={job.status}")

    while job.status not in ("succeeded", "failed", "cancelled"):
        time.sleep(30)
        job = openai_client.fine_tuning.jobs.retrieve(job.id)
        print(f"  status={job.status}")

    if job.status != "succeeded":
        raise SystemExit(f"Fine-tuning job {job.id} ended with status={job.status}")

    print(f"Fine-tuned model: {job.fine_tuned_model}")
    print("Deploy it and point the agent at the new deployment with:")
    print(
        f"  az cognitiveservices account deployment create --name <foundryAccountName> "
        f"-g <resourceGroup> --deployment-name gpt-5-mini-retrained "
        f"--model-name {job.fine_tuned_model} --model-format OpenAI"
    )
    print("Then redeploy with -AgentModelDeploymentName gpt-5-mini-retrained and re-run agent/setup_agent.py")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
