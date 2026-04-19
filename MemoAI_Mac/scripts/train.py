import os
import yaml
import torch
import argparse
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    TrainingArguments,
    Trainer,
    DataCollatorForSeq2Seq,
)
from peft import (
    get_peft_model,
    LoraConfig,
    prepare_model_for_kbit_training,
)
from datasets import load_dataset
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_config(config_path):
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def prepare_model_and_tokenizer(config):
    model_config = config['model_config']
    
    # 设置设备
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    logger.info(f"Using device: {device}")
    
    # 加载模型和分词器
    model = AutoModelForCausalLM.from_pretrained(
        model_config['model_name_or_path'],
        trust_remote_code=model_config['trust_remote_code'],
        quantization_config=model_config.get('quantization_config'),
        device_map="auto"
    )
    
    tokenizer = AutoTokenizer.from_pretrained(
        model_config['model_name_or_path'],
        trust_remote_code=model_config['trust_remote_code']
    )
    
    # 准备模型进行训练
    model = prepare_model_for_kbit_training(model)
    
    # 配置 LoRA
    peft_config = LoraConfig(
        **config['peft_config']
    )
    
    model = get_peft_model(model, peft_config)
    model.print_trainable_parameters()
    
    return model, tokenizer

def prepare_dataset(config, tokenizer):
    data_config = config['data_config']
    
    # 加载数据集
    dataset = load_dataset(
        'json',
        data_files={
            'train': data_config['train_file'],
            'validation': data_config['val_file']
        }
    )
    
    def preprocess_function(examples):
        conversations = examples['conversations']
        texts = []
        for conv in conversations:
            text = ""
            for message in conv:
                role = message['role']
                content = message['content']
                if role == 'system':
                    text += f"<|system|>\n{content}\n"
                elif role == 'user':
                    text += f"<|user|>\n{content}\n"
                elif role == 'assistant':
                    text += f"<|assistant|>\n{content}\n"
            texts.append(text)
        
        tokenized = tokenizer(
            texts,
            truncation=True,
            max_length=data_config['max_input_length'],
            padding='max_length'
        )
        
        return tokenized
    
    # 处理数据集
    processed_dataset = dataset.map(
        preprocess_function,
        batched=True,
        num_proc=data_config['num_proc'],
        remove_columns=dataset['train'].column_names
    )
    
    return processed_dataset

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=str, required=True, help='Path to config file')
    args = parser.parse_args()
    
    # 加载配置
    config = load_config(args.config)
    
    # 准备模型和分词器
    model, tokenizer = prepare_model_and_tokenizer(config)
    
    # 准备数据集
    dataset = prepare_dataset(config, tokenizer)
    
    # 设置训练参数
    training_args = TrainingArguments(
        **config['training_args']
    )
    
    # 创建数据整理器
    data_collator = DataCollatorForSeq2Seq(
        tokenizer,
        padding=True,
        max_length=config['data_config']['max_input_length']
    )
    
    # 创建训练器
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=dataset['train'],
        eval_dataset=dataset['validation'],
        data_collator=data_collator,
        tokenizer=tokenizer
    )
    
    # 开始训练
    trainer.train()
    
    # 保存模型
    trainer.save_model()
    tokenizer.save_pretrained(config['training_args']['output_dir'])

if __name__ == "__main__":
    main() 