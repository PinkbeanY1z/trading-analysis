from typing import Literal, Annotated
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
    agent_report_retries: int=Field(1,ge=0,le=2)

class ChatRequest(BaseModel):
    message: str=Field(min_length=1,max_length=4000)
    thread_id: str | None=Field(None,pattern=r'^[a-zA-Z0-9_-]{1,80}$')

class Interpretation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    explanations: list[Annotated[str,Field(max_length=240)]]=Field(default_factory=list,max_length=4,description='简洁解释，建议最多两句；数值只引用证据目录中的完整token，不复述所有指标。')
    evidence_ids: list[str]=Field(min_length=1)
    conclusion: Literal['趋势偏多','趋势偏空','周期分歧','需要更多证据']
    primary_timeframe: Literal['1h','4h','1day']
    conclusion_basis: Literal['ma_alignment','price_structure','multi_timeframe','insufficient_evidence']='ma_alignment'

class ReportAttempt(BaseModel):
    model_config=ConfigDict(extra='forbid')
    valid: bool=False
    content_type: Literal['text','unsupported']='text'
    json_shape: Literal['object','non_object','invalid','empty']='empty'
    finish_reason: Literal['stop','length','tool_calls','content_filter','unknown']='unknown'
    proposed_conclusion: Literal['趋势偏多','趋势偏空','周期分歧','需要更多证据'] | None=None
    conclusion_basis: Literal['ma_alignment','price_structure','multi_timeframe','insufficient_evidence'] | None=None
    field_types: dict[str,str]=Field(default_factory=dict)
    extra_field_count: int=0
    evidence_ids: list[str]=Field(default_factory=list)
    unknown_evidence_id_count: int=0
    explanation_checks: list[dict]=Field(default_factory=list)
    issues: list[dict]=Field(default_factory=list)

class ReportValidation(BaseModel):
    valid: bool=False
    attempts: list[ReportAttempt]=Field(default_factory=list)
    referenced_evidence_ids: list[str]=Field(default_factory=list)

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
    validation: ReportValidation=Field(default_factory=ReportValidation)

class ChatResponse(BaseModel):
    thread_id: str
    symbol: str | None=None
    asset_class: Literal['stock','gold'] | None=None
    timeframes: list[str]=[]
    primary_timeframe: Literal['1h','4h','1day'] | None=None
    status_reasons: list[str]=Field(default_factory=list)
    report: Report
    tool_executions: list[Execution]
    data_sources: list[str]
    warnings: list[str]
    status: Literal['success','partial','failed','insufficient_data']
    mode: str
