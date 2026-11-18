"""Recognize direct approval commands, never instructions embedded in prose.

Every clause must be an approval or a review acknowledgement. Unknown clauses,
questions, conditions, negations and quoted examples fall through to consultation.
The model never grants execution permission.
"""
import re
import unicodedata


_FIELDS = r'(?:xml|执行程序|程序|输入文件|输入|输出目录|配置|当前预览|预览|、|和|与|及)*'
_REVIEW_VERB = r'(?:我)?(?:已经|已|都)?(?:核对|检查|确认|看过|看了)(?:过|完成|完毕|好了)?'
_OK = r'(?:没啥问题|没有问题|没问题|无误|没啥毛病|正确|通过|好了)'
_REVIEW = re.compile(rf'(?:{_FIELDS}{_REVIEW_VERB}(?:{_FIELDS})?(?:{_OK})?|{_FIELDS}{_OK})')
_START = re.compile(
    r'(?:好|好的|那|那么)?(?:现在)?(?:请)?(?:现在)?(?:你)?(?:可以|帮我|为我)?'
    r'(?:确认并|确认|确定并|确定)?(?:开始执行|开始运行|开始解算|执行|运行|解算|启动)'
    r'(?:当前|本次|这个|该)?(?:实验|计划|任务|预览|解算)?(?:吧|了)?'
)
_EN_START = re.compile(r'(?:please\s+)?(?:start|run|execute)\s+(?:the\s+|this\s+|current\s+)?experiment(?:\s+now)?')


def is_execution_approval(message: str) -> bool:
    normalized = unicodedata.normalize('NFKC', message).strip().casefold()
    clauses = [part.strip() for part in re.split(r'[,，。.!！;；\n]+', normalized) if part.strip()]
    found = False
    for clause in clauses:
        compact = re.sub(r'\s+', '', clause)
        if _START.fullmatch(compact) or _EN_START.fullmatch(clause):
            found = True
        elif not _REVIEW.fullmatch(compact):
            return False
    return found
