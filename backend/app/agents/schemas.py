from typing import Literal
from pydantic import BaseModel, Field, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

class AgentSettings(BaseSettings):
    model_config=SettingsConfigDict(env_file=Path(__file__).resolve().parents[2]/'.env',extra='ignore')
    agent_mode: Literal['deepseek','mock']='deepseek'
    deepseek_api_key: str=''
    deepseek_base_url: str='https://api.deepseek.com'
    deepseek_model: str='deepseek-flash'
    deepseek_timeout: float=Field(30,gt=0)
    agent_timeout: float=Field(120,gt=0)
    agent_tool_timeout: float=Field(30,gt=0)
    agent_max_rounds: int=Field(6,ge=1,le=20)

class ChatRequest(BaseModel):
    message: str=Field(min_length=1,max_length=4000)
    thread_id: str | None=Field(None,pattern=r'^[a-zA-Z0-9_-]{1,80}$')

class Interpretation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    explanations: list[str]=Field(default_factory=list,max_length=8)
    evidence_ids: list[str]=Field(min_length=1)
    conclusion: Literal['趋势偏多','趋势偏空','周期分歧','需要更多证据']

class Execution(BaseModel):
    call_id: str
    name: str
    arguments: dict
    status: Literal['success','failed']
    result: dict

class Report(BaseModel):
    sections: list[dict]=Field(default_factory=list)
    conclusion: str
    evidence: list[dict]
    interpretation: Interpretation | None=None

class ChatResponse(BaseModel):
    thread_id: str
    symbol: str | None=None
    timeframes: list[str]=[]
    report: Report
    tool_executions: list[Execution]
    data_sources: list[str]
    warnings: list[str]
    status: Literal['success','partial','failed','insufficient_data']
    mode: str
