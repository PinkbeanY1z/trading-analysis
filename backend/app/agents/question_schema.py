"""Question-first protocol: the model selects a plan and cites server-owned values."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Frame = Literal['1h','4h','1day']
QuestionKind = Literal['entry','win_rate','risk_reward','levels','trend','timeframes','stabilization','watch','indicator','general']
ToolName = Literal['get_market_data','calculate_technical_indicators','analyze_market_structure','compare_timeframes','evaluate_trade_plan']


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ResearchQuestion(ClosedModel):
    id: str = Field(pattern=r'^q[1-9][0-9]?$', max_length=3)
    kind: QuestionKind
    question: str = Field(min_length=2,max_length=120,description='本轮用户实际子问题的简短中文表述，不加入无关指标任务。')


class ResearchPlan(ClosedModel):
    questions: list[ResearchQuestion] = Field(min_length=1,max_length=6)
    primary_timeframe: Frame | None = None
    proposed_tools: list[ToolName] = Field(min_length=1,max_length=5,description='按子问题需要选择专项工具，不固定遍历所有工具；缺回测直接承认。')

    @model_validator(mode='after')
    def unique_ids(self):
        if len({q.id for q in self.questions}) != len(self.questions):
            raise ValueError('question ids must be unique')
        if len(set(self.proposed_tools)) != len(self.proposed_tools):
            raise ValueError('proposed tools must be unique')
        return self


class FactSelection(ClosedModel):
    evidence_id: str = Field(min_length=1,max_length=160)
    metric: str = Field(min_length=1,max_length=160,description='逐字复制目录中的metric；数字由服务器取值。')
    label: str = Field(min_length=1,max_length=24)
    format: Literal['price','ratio','percent','volume','number'] = 'number'
    interval: Frame | None = None


class AnswerDraftBlock(ClosedModel):
    kind: Literal['direct_answer','key_evidence','levels','risk_reward','limitation','watch']
    title: str = Field(min_length=1,max_length=32)
    text: str = Field(min_length=1,max_length=600,description='直接回答对应子问题；所有数值使用目录完整引用token，不能手写数值。')
    question_ids: list[str] = Field(min_length=1,max_length=6)
    evidence_ids: list[str] = Field(default_factory=list,max_length=8)
    facts: list[FactSelection] = Field(default_factory=list,max_length=4)
    detail: bool = False


class QuestionAnswerDraft(ClosedModel):
    protocol_version: Literal[2]
    primary_timeframe: Frame | None = None
    evidence_ids: list[str] = Field(min_length=1,max_length=12)
    entry_stance: Literal['not_supported','await_confirmation','conditional_hypothesis','not_applicable'] = 'not_applicable'
    answer_blocks: list[AnswerDraftBlock] = Field(min_length=1,max_length=8)
    trade_plan_evidence_id: str | None = None


class AnswerFact(ClosedModel):
    label: str
    value: float | None
    format: Literal['price','ratio','percent','volume','number']
    evidence_id: str
    metric: str


class AnswerBlock(ClosedModel):
    id: str
    kind: Literal['direct_answer','key_evidence','levels','risk_reward','limitation','watch']
    title: str
    text: str
    question_ids: list[str]
    evidence_ids: list[str]
    facts: list[AnswerFact] = Field(default_factory=list)
    detail: bool = False
