#!/bin/bash

# 显示帮助信息
show_help() {
    echo "MemoAI for Mac - 基于Ollama的微信聊天记录训练方案"
    echo
    echo "使用方法:"
    echo "  ./run.sh [命令]"
    echo
    echo "可用命令:"
    echo "  convert-csv    - 转换CSV格式的聊天记录"
    echo "  convert-html   - 转换HTML/TXT格式的聊天记录"
    echo "  create-model   - 创建Ollama模型"
    echo "  web           - 启动Web界面"
    echo "  help          - 显示此帮助信息"
    echo
    echo "示例:"
    echo "  ./run.sh convert-csv --csv_file chat.csv"
    echo "  ./run.sh convert-html --my_name '张三'"
    echo "  ./run.sh create-model"
    echo "  ./run.sh web"
}

# 检查Python环境
check_python() {
    if ! command -v python3 &> /dev/null; then
        echo "错误: 未找到Python3"
        exit 1
    fi
}

# 检查Ollama是否安装
check_ollama() {
    if ! command -v ollama &> /dev/null; then
        echo "错误: 未找到Ollama，请先安装: https://ollama.ai/"
        exit 1
    fi
}

# 检查依赖是否安装
check_dependencies() {
    if ! pip3 list | grep -q "flask\|requests\|pandas\|beautifulsoup4"; then
        echo "正在安装依赖..."
        pip3 install -r requirements.txt
    fi
}

# 主函数
main() {
    # 检查Python环境
    check_python
    
    # 检查依赖
    check_dependencies
    
    # 如果没有参数，显示帮助信息
    if [ $# -eq 0 ]; then
        show_help
        exit 0
    fi
    
    # 处理命令
    case "$1" in
        convert-csv)
            shift
            python3 scripts/convert_wechat_data_csv.py "$@"
            ;;
        convert-html)
            shift
            python3 scripts/convert_wechat_data.py "$@"
            ;;
        create-model)
            check_ollama
            if [ ! -f "Modelfile" ]; then
                echo "错误: 未找到Modelfile"
                exit 1
            fi
            ollama create my-chat-ai --file Modelfile
            ;;
        web)
            check_ollama
            python3 scripts/web_interface.py
            ;;
        help)
            show_help
            ;;
        *)
            echo "错误: 未知命令 '$1'"
            show_help
            exit 1
            ;;
    esac
}

# 执行主函数
main "$@" 