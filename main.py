import subprocess
import langchain
from langgraph.graph import StateGraph, END, START
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.schema import SystemMessage, HumanMessage
from dotenv import load_dotenv
from typing import Dict, TypedDict
from langchain.tools import BaseTool, StructuredTool, tool
from langchain.agents import create_tool_calling_agent
from langchain.agents import AgentExecutor
from langchain_core.prompts import ChatPromptTemplate
from langchain_community.tools import DuckDuckGoSearchResults #searching tools
import os
import re
import shlex
from tenacity import retry, stop_after_attempt, wait_fixed


load_dotenv()
# Khởi tạo Gemini API (thay YOUR_GEMINI_API_KEY bằng key của bạn)
llm = ChatGoogleGenerativeAI(model="learnlm-1.5-pro-experimental",
                             verbose=True,
                             temperature=0.5,
                             google_api_key=os.getenv("GOOGLE_API_KEY"))

class State(TypedDict):
    prompt: str
    code: str
    output: str

@tool
def run_command(command: str) -> str:
    """Executes a shell command and returns its output."""
    result = subprocess.run(shlex.split(command), shell=True, capture_output=True, text=True)
    return (result.stdout or "") + (result.stderr or "")

@tool
def save_file_python(code: str, file_path: str) -> bool:
    """Save the python code on local and return save state."""
    try:
        with open(file_path, "w", encoding="utf-8") as file:
            file.write(code)
        print(f"Code saved successfully to '{file_path}'")
        return True
    except Exception as e:
        print(f"An error occurred while saving the code: {e}")
        return False

@tool    
def read_file_python(file_path: str) -> str:
    """Read code in a python file and return code in file on local."""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            file_content = file.read()
        return file_content
    except FileNotFoundError:
        print(f"Error: File '{file_path}' not found.")
        return ""

def extract_text_from_file(file_path):
    try:
        with open(file_path, 'r') as file:
            file_content = file.read()
        return file_content
    except FileNotFoundError:
        print(f"Error: File '{file_path}' not found.")
        return None

def extract_python_code_from_md(md: str):
    pattern = r'```python\s*(.*?)\s*```'
    
    # Trích xuất code với re.DOTALL để khớp với các dòng mới
    python_code_blocks = re.findall(pattern, md, re.DOTALL)
    
    return python_code_blocks[0].strip()

def route_result(state: State):
    try:    
        if "error" in state["output"].lower():
            return "fix_bug"
        else:
            return "end"
    except:
        return "end"

def generate_python_code(query):
    """Sinh mã Python từ prompt bằng Gemini API"""
    system_prompt = "Bạn là một trợ lý AI tạo python chính xác và tối ưu. Không trả lời những yêu cầu không liên quan. Sau khi tạo mã chính xác và sạch đẹp hãy lưu nó vào local. Cuối cùng là trả về file path cho output."
    # response = llm.invoke([
    #     SystemMessage(content="Bạn là một trợ lý AI tạo và chạy mã python chính xác và tối ưu."),
    #     HumanMessage(content=prompt)
    # ])
    prompt = ChatPromptTemplate.from_messages([("system", system_prompt),
            ("placeholder", "{chat_history}"),
            ("human", "{input}"),
            ("placeholder", "{agent_scratchpad}"),])
    
    agent = create_tool_calling_agent(llm, [save_file_python], prompt)
    agent_executor = AgentExecutor(agent=agent, tools=[save_file_python], verbose=True)
    response = agent_executor.invoke({"input": query})
    # return extract_python_code_from_md(response.content)
    return response.get('output')

def run_python_code(file):
    """Chạy mã Python bằng subprocess"""
    system_prompt = """Bạn là chuyên gia chạy mã python trên command line của Windows chính xác, trách nhiệm. Nhận file python, đọc code trong file, pip install những thư viện cần thiết rồi run code. Chỉ trả về kết quả từ command line vào output.
Ví dụ: UnicodeDecodeError: 'charmap' codec can't decode byte 0x9d in position 42: character maps to <undefined>
    """

    query = f"Chạy thành công file python này ở local, đây là file path ở local: {file}."
    
    prompt = ChatPromptTemplate.from_messages([("system", system_prompt),
            ("placeholder", "{chat_history}"),
            ("human", "{input}"),
            ("placeholder", "{agent_scratchpad}"),])
    
    agent = create_tool_calling_agent(llm, [run_command, read_file_python], prompt)
    agent_executor = AgentExecutor(agent=agent, tools=[run_command, read_file_python], verbose=True)
    response = agent_executor.invoke({"input": query})
    # return extract_python_code_from_md(response.content)
    return response.get('output')
    # try:
    #     result = subprocess.run(["python", "-c", code], capture_output=True, text=True)
    #     return result.stdout if result.returncode == 0 else result.stderr
    # except Exception as e:
    #     return str(e)

def fixbug_agent(query, file, error):
    """Fix bug từ mã Python"""
    system_prompt = f"""Bạn là chuyên gia fix bug giàu kinh nghiệm, dày dặn, tỷ mỉ và chính xác.
    Biết  Môi trường chạy code: Windows, CPU Intel i3-10100, RAM 16GB, không có GPU.
    Nhận file code với path {file}, đọc code và lỗi của code hiện tại.
    Yêu cầu người dùng ban đầu là {query}.
    Hãy phân tích, tìm kiếm cách fix lỗi tốt nhất. Sửa vào trong file và lưu và run command để install thư viện mới (nếu cần) và chạy file code đã lưu để kiểm tra.
    Nếu không có lỗi được sửa trả về duy nhất từ 'Complete' vào output. Nếu có lỗi mới thì trả về lỗi hiện thị từ command line vào output."""
    tools = [
        DuckDuckGoSearchResults(name="search_tool"),
        run_command,
        save_file_python,
        read_file_python
    ]
    try_count = 0
    while True:
        e = f"Fix lỗi hiện tại: {error}"
        prompt = ChatPromptTemplate.from_messages([("system", system_prompt),
                ("placeholder", "{chat_history}"),
                ("human", "{input}"),
                ("placeholder", "{agent_scratchpad}"),])
        
        agent = create_tool_calling_agent(llm, tools, prompt)
        agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)
        response = agent_executor.invoke({"input": e})
        
        if 'complete' in response.get("output").lower():
            break
        error = response.get("output")
        try_count += 1
        if try_count > 10:
            break
    return response.get('output')
    

# Xây dựng LangGraph
class CodeAgent:
    def __init__(self):
        self.workflow = StateGraph(State)

        
        self.workflow.add_node("generate", self.generate_code)
        self.workflow.add_node("execute", self.execute_code)
        self.workflow.add_node("fix_bug", self.fix_bug_agent)
        
        self.workflow.add_edge(START, "generate")
        self.workflow.add_edge("generate", "execute")
        self.workflow.add_conditional_edges(
            "execute",
            route_result,
            {
                "fix_bug": "fix_bug",
                "end": END
            }
        )
        self.workflow.add_edge("fix_bug", END)
        
        self.workflow.set_entry_point("generate")
        self.app = self.workflow.compile()

    def generate_code(self, state: State) -> State:
        state["code"] = generate_python_code(state["prompt"])
        return state
    
    def execute_code(self, state: State) -> State:
        state["output"] = run_python_code(state["code"])
        return state

    def fix_bug_agent(self, state: State) -> State:
        state["output"] = fixbug_agent(state["prompt"], state["code"], state["output"])
        return state
    
    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))  # Thử lại tối đa 3 lần, chờ 2s mỗi lần
    def run(self, prompt):
        return self.app.invoke({"prompt": prompt})

# Khởi chạy agent
if __name__ == "__main__":
    agent = CodeAgent()
    user_prompt = input("Nhập yêu cầu code Python: ")
    result = agent.run(user_prompt)
    print("\nGenerated Code:\n", result["code"])
    print("\nExecution Output:\n", result["output"])
