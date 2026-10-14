"""GPU-only BF16 LoRA pilot. Adapter checkpoints only; no full-model saves."""
import fcntl
import json
import math
import os
import random
import signal
import time
from pathlib import Path

from . import dense_benchmark as b
from .dense_lora import MODEL, REVISION, check_runtime


def load_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def targets(model):
    # Explicitly exclude the vision tower, projector, embeddings and output head.
    suffixes = {'q_proj','k_proj','v_proj','o_proj','gate_proj','up_proj','down_proj'}
    names = [name for name,module in model.named_modules()
             if name.startswith('model.language_model.layers.') and name.rsplit('.',1)[-1] in suffixes]
    if not names: raise ValueError('No audited Glimmer text projection targets found')
    return names


def save_adapter(model, work, destination, trial):
    destination.mkdir(parents=True,exist_ok=True)
    model.save_pretrained(destination,safe_serialization=True,save_embedding_layers=False)
    config=b.read(destination/'adapter_config.json')
    config.update(base_model_name_or_path=MODEL,revision=REVISION)
    b.write(destination/'adapter_config.json',config)
    b.write(destination/'provenance.json',{'model':MODEL,'revision':REVISION,'trial':trial,
        'manifest_sha256':b.sha(work/'manifest.json'),'tokenizer':'../../../tokenizer',
        'training_environment':'../../../results/training-environment.txt',
        'format':'PEFT adapter only; load with pinned BF16 base, not the FP8 inference artifact'})
    from safetensors import safe_open
    with safe_open(destination/'adapter_model.safetensors',framework='pt',device='cpu') as f:
        keys=list(f.keys())
        if not keys or any('lora_' not in key for key in keys):raise ValueError('Unexpected non-adapter weights')
        import torch
        if not all(torch.isfinite(f.get_tensor(k)).all().item() for k in keys):raise ValueError('Non-finite saved adapter')
    return {str(p.relative_to(work)):b.sha(p) for p in destination.iterdir() if p.is_file()}


def batch(row, torch):
    return {'input_ids':torch.tensor([row['input_ids']],device='cuda'),
            'labels':torch.tensor([row['labels']],device='cuda'),
            'attention_mask':torch.ones((1,len(row['input_ids'])),dtype=torch.long,device='cuda')}


def eval_loss(model, rows, torch, deadline):
    model.eval();total=0.;tokens=0
    with torch.no_grad():
        for row in rows:
            if time.monotonic()>deadline:raise TimeoutError('Validation reserve exhausted')
            output=model(**batch(row,torch),use_cache=False)
            value=float(output.loss.detach());n=sum(x!=-100 for x in row['labels'])
            if not math.isfinite(value):raise ValueError('Non-finite validation loss')
            total+=value*n;tokens+=n
            del output
    return total/tokens


def preview(model, tokenizer, rows, work, name, torch, deadline, maximum):
    """Small generation diagnostic, identical subset for baseline and adapters."""
    model.eval();results=[]
    for row in rows:
        if time.monotonic()>deadline:raise TimeoutError('Generation reserve exhausted')
        start=time.monotonic()
        inputs=torch.tensor([row['prompt_ids']],device='cuda')
        with torch.no_grad():
            output=model.generate(input_ids=inputs,attention_mask=torch.ones_like(inputs),
                do_sample=False,max_new_tokens=maximum,use_cache=True,
                max_time=max(.1,deadline-time.monotonic()),
                pad_token_id=tokenizer.eos_token_id,eos_token_id=tokenizer.convert_tokens_to_ids('<|eot|>'))
        ids=output[0,inputs.shape[1]:].tolist()
        text=tokenizer.decode(ids,skip_special_tokens=False)
        results.append({k:row[k] for k in ('article_id','patient_id','task')} | {
            'text':text,'seconds':time.monotonic()-start,'generated_tokens':len(ids),
            'hit_token_limit':len(ids)>=maximum,'target':row['target']})
        b.write_rows(work/'results'/name/'generations.jsonl',results)
        del inputs,output
    return {'examples':len(results),'seconds':sum(r['seconds'] for r in results),
            'generated_tokens':sum(r['generated_tokens'] for r in results),
            'hit_token_limit':sum(r['hit_token_limit'] for r in results)}


def run(work, trial_index=None):
    if not os.environ.get('SLURM_JOB_ID') or not os.environ.get('CUDA_VISIBLE_DEVICES'):
        raise ValueError('Refuse training outside allocated Slurm GPU')
    import torch
    from transformers import MuseGlimmerForConditionalGeneration, AutoTokenizer
    from peft import LoraConfig, get_peft_model, PeftModel
    from .experiment_logging import start as start_logging
    check_runtime(work)
    if torch.cuda.device_count()!=1 or torch.cuda.get_device_capability(0)!=(10,0):
        raise ValueError('Expected exactly one B200 GPU')
    plan=b.read(work/'manifest.json')['lora'];smoke=trial_index is None
    if not smoke and not 0 <= trial_index < len(plan['trials']): raise ValueError('Unknown trial index')
    trial={'name':'smoke','rank':32,'lr':5e-5,'epochs':1,'seed':42} if smoke else plan['trials'][trial_index]
    name=trial['name'];out=work/'results'/name
    if (out/'receipt.json').exists():raise ValueError('Trial already attempted; use a new campaign')
    if not smoke and not b.read(work/'results/smoke/receipt.json').get('smoke_passed'):
        raise ValueError('GPU smoke failed; do not run training experiments')
    out.mkdir(parents=True,exist_ok=True)
    lock=(work/'runtime/model.lock').open('a');fcntl.flock(lock,fcntl.LOCK_SH)
    started=time.monotonic();wall=time.time()
    end_unix=int(os.environ.get('SLURM_JOB_END_TIME') or (wall+(2 if smoke else plan['hours'])*3600))
    deadline=started+max(1,end_unix-wall-300)
    receipt={'status':'failed','trial':trial,'adapter_reloaded':False,'started_unix':wall}
    tracking=None;model=None
    def terminated(signum, frame): raise InterruptedError('Slurm termination signal')
    old_handler=signal.signal(signal.SIGTERM,terminated)
    try:
        tracking=start_logging(work,name,{'model':MODEL,'revision':REVISION,**trial,
            'max_length':plan['max_length'],'loss':'completion-only','microbatch':1,
            'gradient_accumulation':plan['accumulation'],'dtype':'bfloat16'})
        random.seed(trial['seed']);torch.manual_seed(trial['seed']);torch.cuda.manual_seed_all(trial['seed'])
        torch.cuda.reset_peak_memory_stats()
        tokenizer=AutoTokenizer.from_pretrained(work/'tokenizer',local_files_only=True)
        model=MuseGlimmerForConditionalGeneration.from_pretrained(work/'runtime/base',
            local_files_only=True,dtype=torch.bfloat16,device_map={'':'cuda:0'},attn_implementation='sdpa')
        target_names=targets(model)
        model=get_peft_model(model,LoraConfig(r=trial['rank'],lora_alpha=2*trial['rank'],lora_dropout=.05,
            target_modules=target_names,bias='none',task_type='CAUSAL_LM'))
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant':False})
        model.enable_input_require_grads()
        trainable=[(n,p) for n,p in model.named_parameters() if p.requires_grad]
        if not trainable or any('lora_' not in n for n,p in trainable):raise ValueError('Base parameters unexpectedly trainable')
        receipt['trainable_parameters']=sum(p.numel() for _,p in trainable)
        b.write(out/'target-modules.json',target_names)
        train=load_rows(work/'tokenized/train.jsonl');validation=load_rows(work/'tokenized/eval.jsonl')
        previews=load_rows(work/'tokenized/preview.jsonl')
        optimizer=torch.optim.AdamW([p for _,p in trainable],lr=trial['lr'],weight_decay=.01)
        step=0;best=math.inf;history=[];completed_epochs=0
        if smoke:
            # Test the largest eligible training sequence, not a toy short input.
            rows=[max(train,key=lambda r:len(r['input_ids']))]
            epochs=1;accumulation=1
        else:
            rows=train;epochs=trial['epochs'];accumulation=plan['accumulation']
        for epoch in range(epochs):
            model.train();order=list(rows);random.Random(trial['seed']+epoch).shuffle(order)
            for offset in range(0,len(order),accumulation):
                if time.monotonic()>deadline-(600 if smoke else 1800):raise TimeoutError('Training stopped to preserve evaluation/save reserve')
                group=order[offset:offset+accumulation];optimizer.zero_grad(set_to_none=True)
                loss_sum=0.;step_start=time.monotonic()
                for row in group:
                    output=model(**batch(row,torch),use_cache=False)
                    loss=output.loss
                    if not torch.isfinite(loss):raise ValueError('Non-finite training loss')
                    loss_sum+=float(loss.detach());(loss/len(group)).backward()
                    del output,loss
                norm=torch.nn.utils.clip_grad_norm_([p for _,p in trainable],1.)
                if not torch.isfinite(norm) or float(norm)==0:raise ValueError('Non-finite or zero adapter gradient')
                optimizer.step();step+=1
                record={'step':step,'epoch':epoch+1,'train/loss':loss_sum/len(group),
                        'train/grad_norm':float(norm),'timing/step_seconds':time.monotonic()-step_start,
                        'gpu/peak_allocated_gib':torch.cuda.max_memory_allocated()/2**30}
                history.append(record);b.write_rows(out/'metrics.jsonl',history);tracking.log(record,step=step)
            value=eval_loss(model,validation[:2] if smoke else validation,torch,deadline)
            tracking.log({'eval/loss':value,'epoch':epoch+1},step=step)
            completed_epochs=epoch+1
            files=save_adapter(model,work,work/'adapters'/name/f'epoch-{epoch+1}',trial)
            if value<best:
                best=value;receipt['adapter_files']=save_adapter(model,work,work/'adapters'/name/'best',trial)
            b.write(out/'progress.json',{'epochs':completed_epochs,'steps':step,'best_eval_loss':best})
        if smoke and not any(bool(torch.any(p.detach()!=0)) for n,p in trainable if 'lora_B' in n):
            raise ValueError('Smoke optimizer did not update any LoRA B weights')
        # Prove the on-disk adapter reloads before declaring success or deleting base.
        del optimizer,trainable
        base=model.unload()
        model=PeftModel.from_pretrained(base,work/'adapters'/name/'best',is_trainable=False)
        model.gradient_checkpointing_disable()
        model.eval();receipt['adapter_reloaded']=True
        smoke_generation=preview(model,tokenizer,previews[:1],work,name+'-reload-check',torch,deadline,32)
        if smoke_generation['generated_tokens']==0:raise ValueError('Reloaded adapter generated no tokens')
        if smoke:
            # One matched BF16 baseline for the diagnostic subset; no new Glimmer
            # full-corpus benchmark. Adapter is disabled within the same process.
            with model.disable_adapter():
                generation=preview(model,tokenizer,previews,work,'baseline',torch,deadline,plan['max_new_tokens'])
            receipt['smoke_passed']=True
        else:
            generation=preview(model,tokenizer,previews,work,name,torch,deadline,plan['max_new_tokens'])
        tracking.log({'generation/'+k:v for k,v in generation.items()},step=step)
        receipt.update(status='complete',best_eval_loss=best,epochs=completed_epochs,steps=step,
                       generation=generation,peak_gpu_gib=torch.cuda.max_memory_allocated()/2**30)
    except BaseException as exc:
        receipt.update(error_type=type(exc).__name__,error=str(exc)[:1500])
        # Completed epoch adapters survive all later failures. No base deletion
        # on failure; no unvalidated partial adapter is advertised as complete.
        raise
    finally:
        receipt.update(seconds=time.monotonic()-started,ended_unix=time.time())
        try:
            if tracking is not None:tracking.finish(exit_code=0 if receipt['status']=='complete' else 1)
        except Exception as exc:
            receipt['tracking_finish_error']=type(exc).__name__
        finally:
            b.write(out/'receipt.json',receipt)
            signal.signal(signal.SIGTERM,old_handler)
            lock.close()
    return receipt
