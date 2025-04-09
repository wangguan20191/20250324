import streamlit as st
import requests
import paramiko
import time
import re
from io import StringIO

# DeepSeek API配置
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
API_KEY = "sk-82879fc3191d4718894b5d96ca96da06"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}


# 生成Cisco命令（优化prompt）
def generate_cisco_command(nl_text):
    prompt = f"""你是一个CISCO网络专家，请将以下需求转换为可立即执行的CISCO IOS命令：
    要求：
    1. 每行一条完整命令
    2. 不要解释
    3. 标记危险命令（在行首加#DANGER）
    示例：
    输入：重启设备
    输出：
    #DANGER
    reload

    输入：{nl_text}"""

    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0
    }

    try:
        response = requests.post(DEEPSEEK_API_URL, headers=HEADERS, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        st.error(f"生成命令失败: {str(e)}")
        return None


# 优化后的SSH执行函数（完整回显）
def ssh_execute_optimized(host, username, password, commands, timeout=15):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        # 连接参数优化
        client.connect(host, port=22, username=username, password=password,
                       timeout=timeout, banner_timeout=20, auth_timeout=15)

        # 创建交互式shell
        chan = client.invoke_shell(term='vt100', width=200, height=1000)
        chan.settimeout(timeout)

        # 清空初始缓冲
        time.sleep(1)
        while chan.recv_ready():
            chan.recv(65535)

        output = ""
        for cmd in commands.split('\n'):
            if not cmd.strip() or cmd.startswith('#'):
                continue

            # 发送命令
            chan.send(cmd + '\n')

            # 实时读取输出（核心优化）
            cmd_output = ""
            start_time = time.time()
            while time.time() - start_time < timeout:
                if chan.recv_ready():
                    data = chan.recv(65535).decode('utf-8', errors='ignore')
                    cmd_output += data
                    # 检测是否出现设备提示符
                    if re.search(r"[\w-]+(\(config\))?#|>", cmd_output):
                        break
                else:
                    time.sleep(0.1)

            output += f">>> {cmd}\n{cmd_output}\n"

        return output
    except Exception as e:
        raise Exception(f"SSH执行错误: {str(e)}")
    finally:
        client.close()


# Streamlit界面
def main():
    st.set_page_config(page_title="Cisco智能运维终端", layout="wide")
    st.title("🚀 Cisco 智能运维终端 (优化版)")

    # 设备连接配置
    with st.sidebar:
        st.subheader("⚙️ 设备设置")
        host = st.text_input("设备IP", "192.168.1.1")
        username = st.text_input("用户名", "admin")
        password = st.text_input("密码", type="password")
        exec_mode = st.radio("执行模式", ["模拟测试", "真实设备"])

    # 主界面
    user_input = st.text_area("📝 输入配置需求", height=150,
                              placeholder="例：配置Gig0/1端口的IP为192.168.1.1/24，启用OSPF")

    if st.button("生成命令"):
        if not user_input:
            st.warning("请输入配置需求！")
        else:
            with st.spinner("🔍 生成命令中..."):
                commands = generate_cisco_command(user_input)

            if commands:
                st.session_state.generated_commands = commands
                st.success("✅ 生成的命令：")
                st.code(commands, language="bash")

                # 危险命令检测
                if "#DANGER" in commands:
                    st.error("⚠️ 警告：检测到高风险命令（如重启/删除配置）")

    # 二次确认执行
    if 'generated_commands' in st.session_state and exec_mode == "真实设备":
        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            if st.button("❌ 取消执行", use_container_width=True):
                st.session_state.pop('generated_commands')
                st.rerun()
        with col2:
            if st.button("✅ 确认执行", type="primary", use_container_width=True):
                result_placeholder = st.empty()
                with st.spinner("🚀 正在执行命令..."):
                    try:
                        result = ssh_execute_optimized(
                            host, username, password,
                            st.session_state.generated_commands
                        )
                        result_placeholder.code(result, language="text")
                        st.success("执行完成！")
                    except Exception as e:
                        st.error(f"执行失败: {str(e)}")
                st.session_state.pop('generated_commands')


if __name__ == "__main__":
    main()