---
pipeline_tag: image-text-to-text
base_model:
- meta-models/Muse-Glimmer-30B
license: apache-2.0
library_name: Model Optimizer
tags:
- nvidia
- ModelOpt
- Muse-Glimmer
- quantized
- FP4
- fp4
---

# Model Overview

## Description:
The NVIDIA Muse-Glimmer-30B NVFP4 model is the quantized version of Meta Superintelligence Lab's Muse-Glimmer-30B model, which is an auto-regressive language model that uses an optimized transformer architecture. Muse Glimmer is a dense causal transformer with a dedicated perception encoder for interleaved text and image input, purpose-built for local agentic tasks including multi-step reasoning, tool use, and multimodal understanding. For more information, please check [here](https://huggingface.co/meta-models/Muse-Glimmer-30B). The NVIDIA Muse-Glimmer-30B NVFP4 model is quantized with [Model Optimizer](https://github.com/NVIDIA/Model-Optimizer).

This model is ready for commercial or non-commercial use.  <br>

## Third-Party Community Consideration
This model is not owned or developed by NVIDIA. This model has been developed and built to a third-party's requirements for this application and use case; see link to Non-NVIDIA [(Muse-Glimmer-30B) Model Card](https://huggingface.co/meta-models/Muse-Glimmer-30B) from Meta.

### License/Terms of Use:
**GOVERNING DOWNLOAD TERMS:** Use of the model is governed by the [Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0)

### Deployment Geography:
Global <br>

### Use Case: <br>
Developers building autonomous agentic systems, multi-step planning agents, coding agents, and multimodal reasoning applications requiring reliable tool use and failure recovery on consumer hardware.<br>

### Release Date:  <br>
Hugging Face 09/01/2026 via https://huggingface.co/nvidia/Muse-Glimmer-30B-NVFP4 <br>

## References
NVIDIA Model Optimizer: https://github.com/NVIDIA/Model-Optimizer

## Model Architecture:
**Architecture Type:** Transformers  <br>
**Network Architecture:** Dense Causal Transformer with Perception Encoder (`MuseGlimmerForConditionalGeneration`) <br>
**Number of Model Parameters:** 29.6B (including vision encoder) <br>
**This model was developed based on [Muse-Glimmer-30B](https://huggingface.co/meta-models/Muse-Glimmer-30B)** <br>


## Input:
**Input Type(s):** Text, Image <br>
**Input Format(s):** 
Text : String
Image : Red, Green, Blue (RGB) <br>
**Input Parameters:** One-Dimensional (1D), Two-Dimensional (2D) <br>
**Other Properties Related to Input:** Context length up to 131K. The model accepts interleaved text and images through a dedicated perception encoder, enabling multimodal understanding for tasks such as interpreting screenshots, charts, and documents alongside conversation, with up to 4,096 visual tokens per image. The model is not explicitly optimized for video; video input is processed as individual frames. Knowledge cutoff is January 4, 2026. <br>

## Output:
**Output Type(s):** Text <br>
**Output Format:** String <br>
**Output Parameters:** One-Dimensional (1D): Sequences <br>
**Other Properties Related to Output:** The model generates text outputs suitable for agentic task completion, coding agents, and multimodal reasoning. It supports controllable reasoning strength (low / medium / high / xhigh) to balance quality and speed, and includes failure recovery mechanisms for tool use. <br>

Our AI models are designed and/or optimized to run on NVIDIA GPU-accelerated systems. By leveraging NVIDIA's hardware (e.g. GPU cores) and software frameworks (e.g., CUDA libraries), the model achieves faster training and inference times compared to CPU-only solutions. <br>

## Software Integration:
**Supported Runtime Engine(s):** <br>
* **vLLM** <br>
* **SGLang** <br>

**Supported Hardware Microarchitecture Compatibility:** <br>
* NVIDIA Blackwell B200 <br>
* NVIDIA Blackwell B300 <br>
* NVIDIA DGX Spark <br>

**Preferred Operating System(s):** <br>
* Linux <br>

The integration of foundation and fine-tuned models into AI systems requires additional testing using use-case-specific data to ensure safe and effective deployment. Following the V-model methodology, iterative testing and validation at both unit and system levels are essential to mitigate risks, meet technical and functional requirements, and ensure compliance with safety and ethical standards before deployment.

## Model Version(s):
The model version is NVFP4 1.0 version and is quantized with nvidia-modelopt **v0.46.0**.<br>

## Training and Evaluation Datasets:

## Calibration Dataset:
**Link:** [Nemotron-Post-Training-Dataset-v3](https://huggingface.co/collections/nvidia/nemotron-post-training-v3) <br>
**Data Modality:** Text <br>
**Data Collection Method by dataset:** Automated. <br>
**Labeling Method by dataset:** Automated. <br>
**Properties:** The Nemotron-Post-Training-Dataset-v3 a multi-million-sample corpus developed by NVIDIA for Supervised Fine-Tuning (SFT) and Reinforcement Learning (RL) to power alignment, reasoning, and agentic capabilities in the Nemotron-3 model family <br>

## Training Dataset:
**Data Modality:** Undisclosed <br>
**Data Collection Method by dataset:** Undisclosed <br>
**Labeling Method by dataset:** Undisclosed <br>
**Properties:** Undisclosed<br>
**Image Training Data Size:** Undisclosed<br>
**Text Training Data Size:** Undisclosed<br>
**Video Training Data Size:** Undisclosed<br>

## Evaluation Dataset:
**Datasets:** Terminal-Bench 2.1, GDPVal, GPQA Diamond, MMMU Pro, SciCode, IFBench, and AA-LCR <br>
**Data Collection Method by dataset:** Hybrid: Automated, Manually-Collected <br>
**Labeling Method by dataset:** Hybrid: Manually-Labeled, Automated <br>
**Properties:** We evaluated the model on text-based reasoning, coding, agentic tool-use, and multimodal benchmarks: Terminal-Bench 2.1 is an open-source evaluation framework designed to test AI agents on 89 complex, real-world tasks inside sandboxed command-line and container environments. GDPval is an AI evaluation benchmark designed to measure how well frontier models perform real, economically valuable knowledge work across major industries. GPQA Diamond is a specialized benchmark of 198 graduate-level science questions designed to test advanced reasoning and expert knowledge in artificial intelligence. MMMU-Pro is an enhanced, more rigorous version of the MMMU benchmark designed to evaluate genuine multimodal understanding and reasoning in AI models. SciCode is a scientist-curated coding benchmark designed to evaluate the capability of Large Language Models (LLMs) to write code for complex, realistic scientific research problems. IFBench is evaluation benchmark measure how well language models generalize to unseen, out-of-domain (OOD) verifiable output constraints. Artificial Analysis Long Context Reasoning (AA-LCR) measures how well AI models extract, connect, and reason over information across multiple long documents.<br>

## Inference:
**Acceleration Engine:** **vLLM, SGLang** <br>
**Test Hardware:** **NVIDIA Blackwell B200** <br>

## Post Training Quantization
This model was obtained by quantizing Muse-Glimmer-30B to a mixed precision checkpoint with W4A16 NVFP4 weights and FP8 weights and activations. It was quantized using Model Optimizer's [AutoQuantize algorithm](https://github.com/NVIDIA/Model-Optimizer/tree/main/examples/hf_ptq#autoquantize), which searches for the best quantization format per-layer while meeting performance constraints specified by the user. Our Muse Glimmer AutoQuantize recipe searched across MLP, self-attention projection, and language model head layers to assign one of W4A16 NVFP4, FP8, or BF16 precisions across the layers under a 5.5 effective-bit constraint in the selected layers. This optimization reduces the overall effective bits per parameter from 16 to 6.6, reducing the disk size by approximately 2.4x (from 60GB to 24.7 GB).

For more information on the Muse Glimmer AutoQuantize recipe, see our recipe [here](https://github.com/NVIDIA/Model-Optimizer/blob/main/modelopt_recipes/models/meta-models/Muse-Glimmer-30B/auto_quantize/w4a16_nvfp4_4o6_mixed.yaml).

### Quantization strategy

AutoQuant searched a per-module mixed-precision assignment under a nominal 5.50 effective-bit constraint. Each searched module could use one of three choices:

- **W4A16 NVFP4:** 4-bit NVFP4 weights with group size 16 and BF16 activations.
- **FP8:** FP8 weights and activations.
- **BF16 fallback:** no quantization for modules that were too sensitive to either compressed format.

The search covered these language-model layer families:

- MLP `gate_proj`, `up_proj`, and `down_proj` layers.
- Self-attention `q_proj`, `k_proj`, `v_proj`, `o_proj`, and gated-attention projection layers.
- The language-model output head (`lm_head`).

The perception encoder and unmatched modules remain BF16, and the KV cache is unquantized. The BF16 fallback lets AutoQuant preserve sensitive searched modules while meeting the global bit budget. The exported checkpoint records the selected assignment as ModelOpt `MIXED_PRECISION` metadata in `config.json` and `hf_quant_config.json`.

The search used 512 calibration sequences of length 2,048, batch size 1, from the `nemotron-post-training-v3` mixture. That mixture covers instruction following, science, competitive programming, agentic search, mathematics, software engineering, and multilingual code/math/STEM data. AutoQuant measured module sensitivity on this calibration set and selected the lowest-cost precision assignment satisfying the 5.50-bit constraint. 

| Quantization property | Value |
| :--- | :--- |
| Method | NVIDIA ModelOpt AutoQuant PTQ |
| Recipe | Mixed W4A16 NVFP4 / FP8 / BF16 fallback |
| Effective-bit constraint | 5.50 bits |
| Search candidates | W4A16 NVFP4 / FP8 / BF16 fallback |
| Searched layers | Language-model MLP projections, self-attention projections, and `lm_head` |
| Vision encoder | BF16, not quantized |
| KV cache | Unquantized (`auto` at serving time) |
| Calibration | 512 × 2,048-token sequences, batch size 1 |
| Calibration mixture | `nemotron-post-training-v3` |

## Usage

### vLLM

We tested serving the Muse-Glimmer-30B-NVFP4 with vLLM 0.28.0. The tested command inside that runtime is:

```bash
vllm serve /path/to/muse-glimmer-nvfp4 \
  --served-model-name muse-glimmer-nvfp4 \
  --trust-remote-code \
  --enable-prefix-caching \
  --enable-chunked-prefill \
  --enable-auto-tool-choice \
  --tool-call-parser muse_glimmer \
  --reasoning-parser muse_glimmer \
  --kv-cache-dtype auto \
  --max-model-len 131072 \
  --max-num-seqs 64 \
  --gpu-memory-utilization 0.92 \
  --mamba-cache-mode align
```

### SGLang

We tested text-only serving with SGLang DFlash on one NVIDIA Blackwell B200 GPU. The tested configuration, with local model paths replaced by their public Hugging Face IDs, is:

```bash
sglang serve \
  --model-path nvidia/Muse-Glimmer-30B-NVFP4 \
  --language-model-only \
  --reasoning-parser muse \
  --tool-call-parser muse \
  --kv-cache-dtype fp8_e4m3 \
  --mem-fraction-static 0.85 \
  --host 0.0.0.0 \
  --port 30000 \
  --speculative-algorithm DFLASH \
  --speculative-draft-model-path meta-models/Muse-Glimmer-30B-assistant \
  --speculative-draft-model-quantization fp8
```

For matched quality, send chat-completion requests with `temperature=1.0`, `top_p=0.95`, `top_k=64`, thinking enabled, and high reasoning strength. Tool-use applications should use the packaged `chat_template.jinja` and Muse Glimmer reasoning/tool parsers.

## Evaluation
 The BF16 source and this checkpoint were evaluated with the same policy: high reasoning, temperature 1.0, top-p 0.95, and top-k 64. Terminal-Bench used eight repeats; GPQA Diamond used sixteen; IFBench strict used five. Values marked with `±` report standard error across those repeats. GDPVal used one benchmark repeat, so no repeat-level normalized-Elo standard error is reported.

Some measured scores for the quantized checkpoint are higher than the corresponding BF16 scores. These differences may result from sampling and evaluation noise and should not be interpreted as evidence that quantization increased the model's underlying intelligence or capabilities.

| Benchmark | BF16 | muse-glimmer-nvfp4 | Delta |
| :--- | ---: | ---: | ---: |
| Terminal-Bench 2.1 pass@1 | 45.22 ± 0.92 | **47.05 ± 1.42** | +1.83 pp |
| GDPVal normalized Elo | 0.20282 | **0.23798** | +0.03516 |
| GPQA Diamond pass@1 | **83.81 ± 0.33** | 83.02 ± 0.40 | -0.79 pp |
| MMMU-Pro | **74.22** | 73.58 | -0.64 pp |
| SciCode subtask accuracy | 47.63 | **49.70** | +2.07 pp |
| IFBench strict pass@1 | 76.58 ± 0.53 | **78.74 ± 0.52** | +2.16 pp |
| AA-LCR mean reward | **76.25** | 75.56 | -0.69 pp |

## Model Limitations:
The base model was trained on data that may contain toxic language and societal biases originally crawled from the internet. Therefore, the model may amplify those biases and return toxic responses especially when prompted with toxic prompts. The model may generate answers that may be inaccurate, omit key information, or include irrelevant or redundant text producing socially unacceptable or undesirable text, even if the prompt itself does not include anything explicitly offensive.

## Ethical Considerations

NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. Developers should work with their internal model team to ensure this model meets requirements for the relevant industry and use case and addresses unforeseen product misuse.

Please make sure you have proper rights and permissions for all input image and video content; if image or video includes people, personal health information, or intellectual property, the image or video generated will not blur or maintain proportions of image subjects included.

Please report model quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail).
