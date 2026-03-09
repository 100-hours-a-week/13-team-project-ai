import os
import torch
import gc
from PIL import Image
from transformers import Qwen3VLForConditionalGeneration, AutoProcessor, TrainingArguments, Trainer, DataCollatorForSeq2Seq
from peft import LoraConfig, get_peft_model
from datasets import load_dataset

# 1. 환경 설정 및 경로 정의
BASE_DIR = os.getenv("RUNPOD_VOLUME_PATH", "/workspace") 

# 허깅페이스 캐시 경로
os.environ["HF_HOME"] = os.path.join(BASE_DIR, ".cache/huggingface")

model_id = "Qwen/Qwen3-VL-4B-Instruct"
dataset_path = os.path.join(BASE_DIR, "data2/moyobab_train.jsonl")
image_dir = os.path.join(BASE_DIR, "data2/images")  
output_dir = os.path.join(BASE_DIR, "qwen3_checkpoints")
final_model_path = os.path.join(BASE_DIR, "qwen3_final_full_model")

# 2. 모델 및 프로세서 로드
print(" 모델과 프로세서를 로드하는 중...")
model = Qwen3VLForConditionalGeneration.from_pretrained(
    model_id,
    torch_dtype=torch.bfloat16,
    attn_implementation="flash_attention_2",
    device_map="auto",
    trust_remote_code=True
)
processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)

# 3. 데이터 전처리 함수 
def preprocess_fn(example):
    img_path = os.path.join("/workspace", example["image"])
    try:
        image = Image.open(img_path).convert("RGB")

        # 메시지 구성
        prompt_text = "이미지에 적힌 메뉴를 모두 추출해줘."
        messages = [
            {"role": "user", "content": [{"type": "image", "image": img_path}, {"type": "text", "text": prompt_text}]},
            {"role": "assistant", "content": [{"type": "text", "text": example["label"]}]}
        ]

        # 1. 전체 텍스트 템플릿 적용
        full_text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)

        # 2. 질문 부분(User)까지만의 텍스트 템플릿 적용
        user_messages = messages[:1]
        prompt_text_only = processor.apply_chat_template(user_messages, tokenize=False, add_generation_prompt=True)

        # 3. 전체 및 프롬프트 토크나이징
        inputs = processor(text=[full_text], images=[image], return_tensors="pt")
        prompt_inputs = processor(text=[prompt_text_only], images=[image], return_tensors="pt")

        # 4. 라벨 복사 및 마스킹
        labels = inputs["input_ids"].clone()
        prompt_length = prompt_inputs["input_ids"].shape[1]

        # 질문에 해당하는 토큰들을 -100으로 채워 손실 계산에서 제외
        labels[:, :prompt_length] = -100

        # 패딩 토큰도 -100 처리
        if processor.tokenizer.pad_token_id is not None:
            labels[labels == processor.tokenizer.pad_token_id] = -100

        inputs["labels"] = labels
        return {k: v.squeeze(0) for k, v in inputs.items()}

    except Exception as e:
        print(f" 에러 발생 ({img_path}): {e}")
        return None

# 데이터셋 로드 및 매핑
print(" 데이터셋 전처리 시작...")
raw_dataset = load_dataset("json", data_files=dataset_path, split="train")
train_dataset = raw_dataset.map(
    preprocess_fn,
    batched=False,
    remove_columns=raw_dataset.column_names,
    desc="Qwen3-VL 정밀 포맷 변환"
)

# 4. LoRA 설정 (MLP 레이어까지 확장)
config = LoraConfig(
    r=16,
    lora_alpha=32,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    lora_dropout=0.05,
    bias="none",
    task_type="CAUSAL_LM"
)
model = get_peft_model(model, config)
model.print_trainable_parameters()

# 5. 학습 설정 
training_args = TrainingArguments(
    output_dir=output_dir,
    per_device_train_batch_size=4,
    gradient_accumulation_steps=4,
    learning_rate=2e-5,
    num_train_epochs=1,
    bf16=True,
    logging_steps=10,
    save_strategy="steps",
    save_steps=100,
    save_total_limit=2,
    remove_unused_columns=False,
    report_to="none",
    optim="adamw_torch_fused"          
)

# 6. 학습 실행
print(" 파인튜닝을 시작합니다!")
data_collator = DataCollatorForSeq2Seq(
    processor.tokenizer,
    model=model,
    padding=True,
    label_pad_token_id=-100
)

trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=train_dataset,
    data_collator=data_collator,
)

trainer.train()

# 7. 모델 병합 및 최종 저장
print(" 학습 완료! 모델 병합 중...")
del trainer
gc.collect()
torch.cuda.empty_cache()

merged_model = model.merge_and_unload()
merged_model.save_pretrained(final_model_path, safe_serialization=True)
processor.save_pretrained(final_model_path)

print(f" 모든 과정 완료! 최종 모델: {final_model_path}")