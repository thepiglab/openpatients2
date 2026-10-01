#!/usr/bin/env bash
# Check the existing image before downloading any checkpoint.
#SBATCH --job-name=op2-container-check
#SBATCH --account=cai5724
#SBATCH --qos=cai5724
#SBATCH --partition=hpg-b200
#SBATCH --gres=gpu:b200:1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:10:00
#SBATCH --no-requeue
set -euo pipefail
image_path="${1:?Pass the existing vllm.sif path}"
if ! command -v apptainer >/dev/null; then module load apptainer; fi
apptainer exec --nv --cleanenv \
  --env "CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:?GPU allocation required}" \
  --env HF_HUB_OFFLINE=1 --env TRANSFORMERS_OFFLINE=1 \
  "$image_path" /usr/bin/python3 - <<'PY'
import torch
print("Torch:", torch.__version__, flush=True)
count = torch.cuda.device_count()
print("Visible GPUs:", count, flush=True)
assert count > 0, "No CUDA GPUs visible"
capabilities = [torch.cuda.get_device_capability(i) for i in range(count)]
print("GPU capabilities:", capabilities, flush=True)
assert all(cap[0] >= 10 for cap in capabilities), "Expected Blackwell GPUs"
import vllm
print("vLLM:", vllm.__version__, flush=True)
assert vllm.__version__ == "0.30.0", "Expected vLLM 0.30.0"
from vllm.model_executor.models import ModelRegistry
assert "K2HorizonForCausalLM" in ModelRegistry.get_supported_archs(), "Native K2 model unavailable"
from vllm.reasoning import ReasoningParserManager
print("K2 parser:", ReasoningParserManager.get_reasoning_parser("k2_horizon"), flush=True)
print("Container GPU check passed; checkpoint loading and inference remain untested.", flush=True)
PY
