import os
import re
import json
import paramiko
import streamlit as st
from typing import List, Optional, Tuple
from datetime import datetime
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()  # 从.env文件加载（开发环境）

# 安全获取API密钥
def get_api_key() -> str:
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError("DEEPSEEK_API_KEY环境变量未配置")
    if key.startswith("sk-"):
        return key
    raise ValueError("无效的API密钥格式")

# 配置类
class AppConfig:
    API_URL = "https://api.deepseek.com/v1/chat/completions"
    HEADERS = {"Authorization": f"Bearer {get_api_key()}", "Content-Type": "application/json"}
    CISCO_PROMPT_PATTERN = r"[\w-]+(\(config\))?#|>"
    HISTORY_FILE = "connection_history.json"

# 命令生成模块
class CommandGenerator:
    @staticmethod
    def generate(nl_text: str) -> Optional[str]:
        """安全生成Cisco命令"""
        prompt = f"""作为思科网络专家，请严格按以下要求转换配置命令：
        1. 仅返回有效的IOS命令
        2. 每行一条命令
        3. 危险命令添加#DANGER前缀
        4. 包含必要的模式切换
        
        示例输入：重启核心交换机
        示例输出：
        #DANGER
        reload
        
        实际输入：{nl_text}"""

        try:
            response = requests.post(
                AppConfig.API_URL,
                headers=AppConfig.HEADERS,
                json={
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1
                },
                timeout=15
            )
            response.raise_for_status()
            return CommandValidator.sanitize(
                response.json()["choices"][0]["message"]["content"]
            )
        except Exception as e:
            st.error(f"命令生成失败: {str(e)}")
            return None

# 命令验证模块
class CommandValidator:
    DANGER_COMMANDS = {"reload", "erase", "delete"}

    @classmethod
    def sanitize(cls, raw: str) -> str:
        """命令安全过滤"""
        cleaned = []
        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            if any(cmd in line for cmd in cls.DANGER_COMMANDS):
                cleaned.append(f"#DANGER {line}")
            else:
                cleaned.append(line)
        return "\n".join(cleaned)

# 设备连接模块
class CiscoConnector:
    def __init__(self, host: str, user: str, password: str):
        self.host = host
        self.user = user
        self.password = password
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def __enter__(self):
        try:
            self.client.connect(
                self.host,
                username=self.user,
                password=self.password,
                timeout=15,
                banner_timeout=20
            )
            return self
        except Exception as e:
            raise ConnectionError(f"连接失败: {str(e)}")

    def exec_commands(self, commands: List[str]) -> str:
        """安全执行命令"""
        output = []
        with self.client.invoke_shell() as chan:
            for cmd in commands:
                if cmd.startswith("#"):
                    continue
                chan.send(f"{cmd}\n")
                output.append(self._read_output(chan))
        return "\n".join(output)

    def _read_output(self, chan) -> str:
        """读取命令输出"""
        buf = ""
        while True:
            data = chan.recv(4096).decode("utf-8")
            buf += data
            if re.search(AppConfig.CISCO_PROMPT_PATTERN, buf):
                break
        return buf

    def __exit__(self, *args):
        self.client.close()

# 历史记录管理
class HistoryManager:
    @staticmethod
    def load() -> List[dict]:
        try:
            with open(AppConfig.HISTORY_FILE, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    @staticmethod
    def save(history: List[dict]):
        with open(AppConfig.HISTORY_FILE, "w") as f:
            json.dump(history[:10], f, indent=2)

# Streamlit界面
def main():
    st.set_page_config(
        page_title="安全网络运维平台",
        layout="wide",
        page_icon="🔒"
    )
    st.title("🔐 安全网络运维平台")

    # 初始化会话状态
    if "history" not in st.session_state:
        st.session_state.history = HistoryManager.load()
    if "generated" not in st.session_state:
        st.session_state.generated = None

    # 侧边栏
    with st.sidebar:
        st.subheader("设备连接配置")
        host = st.text_input("设备IP", "192.168.1.1")
        user = st.text_input("用户名", "admin")
        password = st.text_input("密码", type="password")

        if st.button("测试连接"):
            try:
                with CiscoConnector(host, user, password):
                    st.success("连接成功!")
            except Exception as e:
                st.error(str(e))

    # 主界面
    with st.form("command_form"):
        req = st.text_area("配置需求", height=100,
                         placeholder="例：配置Gig0/1接口IP为192.168.1.1/24")

        if st.form_submit_button("生成命令"):
            if not req.strip():
                st.warning("请输入配置需求")
            else:
                st.session_state.generated = CommandGenerator.generate(req)

    # 显示生成的命令
    if st.session_state.generated:
        st.divider()
        st.subheader("生成的配置命令")

        with st.expander("命令列表", expanded=True):
            st.code(st.session_state.generated)

            if "#DANGER" in st.session_state.generated:
                st.error("检测到高风险命令，请谨慎操作！")

        # 执行命令
        if st.button("执行命令", type="primary"):
            try:
                with CiscoConnector(host, user, password) as conn:
                    commands = [
                        cmd for cmd in st.session_state.generated.split("\n")
                        if cmd.strip() and not cmd.startswith("#")
                    ]
                    output = conn.exec_commands(commands)

                    st.session_state.history.append({
                        "time": datetime.now().isoformat(),
                        "commands": commands,
                        "output": output[:5000]
                    })
                    HistoryManager.save(st.session_state.history)

                    st.success("执行成功！")
                    with st.expander("查看输出"):
                        st.code(output)
            except Exception as e:
                st.error(f"执行失败: {str(e)}")

    # 显示历史记录
    if st.session_state.history:
        st.divider()
        with st.expander("执行历史"):
            for idx, item in enumerate(reversed(st.session_state.history)):
                st.markdown(f"**{item['time']}**")
                st.code("\n".join(item["commands"]))
                if st.button(f"查看输出 #{len(st.session_state.history)-idx}"):
                    st.code(item["output"])

if __name__ == "__main__":
    main()