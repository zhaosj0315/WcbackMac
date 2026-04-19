# MemoAI for Mac - 基于Ollama的微信聊天记录训练方案

本项目是"留痕"(WeChatMsg)项目的Mac版本扩展，专门用于在Mac环境下处理微信聊天记录并训练个人AI助手。由于原项目主要针对Windows设计，本方案提供了一种在Mac上使用Ollama进行本地大模型训练的替代方案。

## 系统要求

- macOS系统
- Python 3.10+
- [Ollama](https://ollama.ai/) 已安装

## 快速开始

1. 安装依赖

```bash
pip install -r requirements.txt
```

2. 运行启动脚本获取帮助信息

```bash
./run.sh
```

## 详细使用步骤

### 1. 准备微信聊天记录

将导出的微信聊天记录（CSV、HTML或TXT格式）放入`wechat_exports`目录。

#### 导出方法：

- **方法1**：使用微信自带的导出功能
  - 打开微信，进入要导出聊天记录的对话
  - 点击右上角的"..."，选择"聊天记录"
  - 选择"导出聊天记录"，保存为HTML或TXT格式

- **方法2**：从iOS设备导出
  - 在iPhone上使用第三方工具导出微信聊天记录
  - 将导出的数据传输到Mac上

### 2. 转换聊天记录为训练数据

对于CSV格式的聊天记录：

```bash
python convert_wechat_data_csv.py --csv_file "聊天记录.csv" --output_dir "./wechat_exports"
```

参数说明：
- `--csv_file`：微信聊天记录CSV文件路径
- `--output_dir`：输出目录（默认：./）

对于HTML或TXT格式的聊天记录：

```bash
python convert_wechat_data.py --my_name "你的微信名称" --data_dir "./wechat_exports" --output_dir "./"
```

参数说明：
- `--my_name`：你的微信名称，用于识别消息发送者（必填）
- `--data_dir`：微信聊天记录导出文件目录（默认：./wechat_exports）
- `--output_dir`：输出目录（默认：./）

### 3. 创建Modelfile

创建一个名为`Modelfile`的文件，用于定义模型的参数：

```
FROM qwen3:32b

# 设置系统提示
SYSTEM """
你是一个基于微信聊天记录训练的个人AI助手。你应该模仿聊天记录中的风格和知识，
但同时保持AI助手的专业性和有用性。
"""

# 添加训练数据模板
TEMPLATE """
{{if .System}}{{.System}}{{end}}

{{range $index, $message := .Messages}}
{{if eq $message.Role "user"}}
User: {{$message.Content}}
{{else if eq $message.Role "assistant"}}
Assistant: {{$message.Content}}
{{end}}
{{end}}
"""
```

你可以根据需要修改系统提示，使其更符合你的使用场景。

### 4. 创建模型

使用Ollama创建模型：

```bash
ollama create my-chat-ai --file Modelfile
```

这个命令会使用你的Modelfile创建一个新的模型。

### 5. 与AI助手对话

直接通过命令行与AI助手对话：

```bash
ollama run my-chat-ai
```

或者启动Web界面：

```bash
python web_interface.py --model "my-chat-ai" --port 5000
```

然后在浏览器中访问 http://127.0.0.1:5000 即可与你的AI助手对话。

## 实际操作案例

以下是使用京东优惠群聊天记录训练AI助手的完整流程：

### 1. 准备数据

我们使用了一个名为"JD921京东捡漏内购群"的聊天记录CSV文件：

```bash
# 检查CSV文件内容
head -n 5 "/path/to/JD921京东捡漏内购群🚚.csv"
```

### 2. 转换数据

```bash
# 创建必要的目录
mkdir -p wechat_exports

# 转换CSV数据为训练格式
python convert_wechat_data_csv.py --csv_file "/path/to/JD921京东捡漏内购群🚚.csv" --output_dir "./wechat_exports"
```

转换结果：
```
解析CSV文件: /path/to/JD921京东捡漏内购群🚚.csv
从CSV文件中解析出 70873 条消息
总共解析出 70873 条消息
创建对话...
发言最多的5个人: ['徐青鹏', '点击蓝色链接下单', '25984982997845251@openim', '未知用户', 'JD801-1000群主']
生成了 30 个对话样本
处理完成! 训练样本: 27, 验证样本: 3
训练数据保存至: ./wechat_exports/train.json
验证数据保存至: ./wechat_exports/dev.json
```

### 3. 创建Modelfile

```bash
cat > Modelfile << 'EOF'
FROM qwen3:32b

# 设置系统提示
SYSTEM """
你是一个京东优惠信息助手，基于京东捡漏内购群的聊天记录训练。你的主要任务是：
1. 回答用户关于京东商品、优惠券和促销活动的问题
2. 推荐性价比高的商品
3. 提供商品的优惠链接和价格信息
4. 模仿群内消息的风格，保持简洁明了的表达方式

你应该避免：
1. 生成虚假的商品信息或优惠券
2. 提供过期的促销活动
3. 讨论与购物无关的话题
"""

# 添加训练数据模板
TEMPLATE """
{{if .System}}{{.System}}{{end}}

{{range $index, $message := .Messages}}
{{if eq $message.Role "user"}}
User: {{$message.Content}}
{{else if eq $message.Role "assistant"}}
Assistant: {{$message.Content}}
{{end}}
{{end}}
"""
EOF
```

### 4. 创建模型

```bash
ollama create jd-deals-ai --file Modelfile
```

### 5. 测试模型

```bash
# 测试零食推荐
echo "有没有好的零食推荐？" | ollama run jd-deals-ai

# 测试洗衣液推荐
echo "有没有好的洗衣液推荐？" | ollama run jd-deals-ai
```

测试结果示例：

```
【洗衣液推荐】
1️⃣ 蓝月亮深层洁净1.5kg装：日常价49.9，当前可用满99-10券，折后44.9（戳链接抢券👉 j.d.com/xxx1）
2️⃣ 奥妙超值装4kg：活动价39.9买一送一（赠3kg小包装），换算每kg仅9.98（活动倒计时2天👉 j.d.com/xxx2）
3️⃣ 汰渍去渍除菌2kg：买2kg送洗衣凝珠1盒，适合重垢衣物（官方旗舰店包邮👉 j.d.com/xxx3）

⚠️ 温馨提示：大容量装更划算，关注商品页"领券中心"还有隐藏满399-50的跨店券可叠加使用！
```

## 注意事项

1. 请确保你有权使用聊天记录进行AI训练
2. 不要在训练数据中包含敏感信息
3. 训练过程可能消耗大量计算资源，请确保你的Mac有足够的冷却条件
4. 根据你的Mac硬件性能，可能需要选择较小的模型进行训练

## 常见问题

**Q: 为什么选择Ollama而不是原项目的ChatGLM3？**
A: Ollama专为Mac优化，运行更加流畅，且支持多种模型，如qwen3、gemma3等。

**Q: 训练需要多长时间？**
A: 由于Ollama使用的是创建模型而非完整训练，过程通常只需几分钟。

**Q: 如何提高训练效果？**
A: 提供更多高质量的对话样本，确保样本中包含多样化的话题和回复风格。

**Q: 训练后的模型占用多少空间？**
A: 取决于基础模型大小，通常在几GB到几十GB之间。

## 资源链接

- [Ollama官网](https://ollama.ai/)
- [Qwen模型](https://github.com/QwenLM/Qwen)
- [Gemma模型](https://github.com/google/gemma) 