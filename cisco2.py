import streamlit as st
import requests
import paramiko
import time
import re
from typing import Optional, Tuple, Dict, Any

# DeepSeek API configuration
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
API_KEY = "sk-82879fc3191d4718894b5d96ca96da06"  # Consider using environment variables
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

# Constants
CISCO_PROMPT_PATTERN = r"[\w-]+(\(config\))?#|>"
MORE_PROMPT = "--More--"
COMMAND_TIMEOUT = 20
CONNECTION_TIMEOUT = 15


def generate_cisco_command(nl_text: str) -> Optional[str]:
    """Generate Cisco CLI commands from natural language input."""
    prompt = f"""You are a Cisco networking expert. Convert the following request into executable Cisco IOS commands:
    Requirements:
    1. One command per line
    2. No explanations
    3. Mark dangerous commands with #DANGER prefix
    4. Include necessary mode transitions (enable, configure terminal)
    5. Use standard Cisco abbreviations (e.g., 'int' for 'interface')
    6. Handle interface names properly (e.g., Gig0/1)

    Example:
    Input: Restart the device
    Output:
    #DANGER
    reload

    Input: Configure OSPF on Gig0/1
    Output:
    enable
    configure terminal
    interface Gig0/1
    ip ospf 1 area 0

    Input: {nl_text}"""

    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1  # Slightly higher for creativity while maintaining reliability
    }

    try:
        response = requests.post(DEEPSEEK_API_URL, headers=HEADERS, json=payload, timeout=15)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        st.error(f"Command generation failed: {str(e)}")
        return None


def ssh_execute(
        host: str,
        username: str,
        password: str,
        commands: str,
        timeout: int = COMMAND_TIMEOUT
) -> Tuple[Optional[str], Optional[Exception]]:
    """Execute commands on Cisco device via SSH with proper output handling."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        # Enhanced connection parameters
        client.connect(
            host,
            port=22,
            username=username,
            password=password,
            timeout=CONNECTION_TIMEOUT,
            banner_timeout=25,
            auth_timeout=20,
            look_for_keys=False,
            allow_agent=False
        )

        # Create interactive shell with larger buffer
        chan = client.invoke_shell(term='xterm', width=200, height=2000)
        chan.settimeout(timeout)

        # Wait for initial prompt and clear buffer
        output = ""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if chan.recv_ready():
                output += chan.recv(65535).decode('utf-8', errors='ignore')
                if re.search(CISCO_PROMPT_PATTERN, output):
                    break
            else:
                time.sleep(0.5)

        full_output = []
        for cmd in commands.split('\n'):
            if not cmd.strip() or cmd.startswith('#'):
                continue

            # Send command
            chan.send(cmd + '\n')
            time.sleep(0.5)  # Wait for command to be processed

            # Read output with pagination handling
            cmd_output = ""
            start_time = time.time()
            while time.time() - start_time < timeout:
                if chan.recv_ready():
                    data = chan.recv(65535).decode('utf-8', errors='ignore')
                    cmd_output += data

                    # Handle pagination
                    if MORE_PROMPT in cmd_output:
                        chan.send(" ")  # Send space to continue
                        time.sleep(0.3)
                        continue

                    # Check for command completion
                    if re.search(CISCO_PROMPT_PATTERN, cmd_output):
                        break
                else:
                    time.sleep(0.1)

            full_output.append(f">>> {cmd}\n{cmd_output}")

        return "\n".join(full_output), None

    except Exception as e:
        return None, e
    finally:
        client.close()


def main():
    """Main Streamlit application."""
    st.set_page_config(
        page_title="Cisco智能运维终端",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    st.title("🚀 Cisco 智能运维终端 (专业版)")

    # Session state initialization
    if 'generated_commands' not in st.session_state:
        st.session_state.generated_commands = None
    if 'execution_history' not in st.session_state:
        st.session_state.execution_history = []

    # Sidebar configuration
    with st.sidebar:
        st.subheader("⚙️ 设备连接设置")
        host = st.text_input("设备IP", "192.168.1.1", key="host_ip")
        username = st.text_input("用户名", "admin", key="username")
        password = st.text_input("密码", type="password", key="password")
        exec_mode = st.radio(
            "执行模式",
            ["模拟测试", "真实设备"],
            index=0,
            key="exec_mode"
        )

        # Connection test button
        if st.button("测试连接", type="secondary"):
            with st.spinner("正在测试连接..."):
                try:
                    client = paramiko.SSHClient()
                    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    client.connect(
                        host,
                        username=username,
                        password=password,
                        timeout=10
                    )
                    client.close()
                    st.success("连接测试成功!")
                except Exception as e:
                    st.error(f"连接失败: {str(e)}")

    # Main interface
    with st.container():
        user_input = st.text_area(
            "📝 输入配置需求",
            height=150,
            placeholder="例：配置Gig0/1端口的IP为192.168.1.1/24，启用OSPF",
            key="user_input"
        )

        col1, col2 = st.columns([1, 3])
        with col1:
            if st.button("生成命令", type="primary"):
                if not user_input.strip():
                    st.warning("请输入配置需求!")
                else:
                    with st.spinner("AI正在生成命令..."):
                        commands = generate_cisco_command(user_input)
                        if commands:
                            st.session_state.generated_commands = commands
                            st.rerun()

        # Display generated commands
        if st.session_state.generated_commands:
            st.divider()
            st.subheader("生成的配置命令")

            with st.expander("查看命令", expanded=True):
                st.code(st.session_state.generated_commands, language="bash")

                # Danger command warning
                if "#DANGER" in st.session_state.generated_commands:
                    st.error("⚠️ 警告: 检测到高风险命令!")

            # Execution section
            if exec_mode == "真实设备":
                st.divider()
                st.subheader("命令执行")

                confirm = st.checkbox("我确认要执行这些命令", key="exec_confirm")
                if confirm and st.button("执行命令", type="primary"):
                    with st.spinner("正在执行命令..."):
                        result, error = ssh_execute(
                            host,
                            username,
                            password,
                            st.session_state.generated_commands
                        )

                        if error:
                            st.error(f"执行失败: {str(error)}")
                        else:
                            st.success("命令执行成功!")
                            with st.expander("查看完整输出"):
                                st.code(result, language="text")

                            # Add to execution history
                            st.session_state.execution_history.append({
                                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "commands": st.session_state.generated_commands,
                                "output": result[:5000]  # Limit stored output
                            })

                            # Clear generated commands after execution
                            st.session_state.generated_commands = None
                            st.rerun()

    # Execution history
    if st.session_state.execution_history:
        st.divider()
        with st.expander("📜 执行历史记录"):
            for idx, item in enumerate(reversed(st.session_state.execution_history)):
                st.markdown(f"""
                **{item['timestamp']}**
                ```bash
                {item['commands']}
                ```
                """)
                if st.button(f"查看输出 #{len(st.session_state.execution_history) - idx}"):
                    st.code(item['output'], language="text")


if __name__ == "__main__":
    main()