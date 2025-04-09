import streamlit as st
from transformers import pipeline

# 加载预训练的 LLM（如 GPT-2）
generator = pipeline("text-generation", model="gpt2")

# 设置页面标题
st.title("LLM 对话演示")

# 用户输入文本框
user_input = st.text_input("输入你的问题或提示：")

if user_input:
    # 调用模型生成文本
    response = generator(user_input, max_length=100, num_return_sequences=1)
    generated_text = response[0]["generated_text"]

    # 显示生成结果
    st.write("模型回复：")
    st.code(generated_text)