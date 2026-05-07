"""Atomic Agent capability contracts."""

from __future__ import annotations

from tests.capabilities.contracts.schema import (
    CapabilityContract,
    InputSpec,
    OutputSpec,
    QualityThresholds,
)

SECURITY_SCANNER = CapabilityContract(
    agent_name="security-scanner",
    agent_type="atomic",
    description="代码安全漏洞扫描",
    required_inputs={
        "file_path": InputSpec(
            type="str",
            description="待扫描文件路径",
            examples=["src/agent_nexus/platform/agency/llm_client.py"],
        ),
    },
    optional_inputs={
        "language": InputSpec(
            type="str",
            description="编程语言",
            examples=["python"],
            required=False,
        ),
    },
    output_schema={
        "findings": OutputSpec(type="list", min_length=0),
        "summary": OutputSpec(type="dict"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=50,
        required_keywords=["finding"],
        score_threshold=0.7,
    ),
    cli_method="scan_code",
)

CODE_REVIEWER = CapabilityContract(
    agent_name="code-reviewer",
    agent_type="atomic",
    description="代码质量审查",
    required_inputs={
        "file_path": InputSpec(
            type="str",
            description="待审查代码路径",
            examples=["src/agent_nexus/platform/agency/executor.py"],
        ),
    },
    optional_inputs={
        "language": InputSpec(
            type="str",
            description="编程语言",
            examples=["python"],
            required=False,
        ),
    },
    output_schema={
        "issues": OutputSpec(type="list", min_length=0),
        "metrics": OutputSpec(type="dict"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=50,
        required_keywords=["issue"],
        score_threshold=0.7,
    ),
    cli_method="analyze",
)

ACCESSIBILITY_AUDITOR = CapabilityContract(
    agent_name="accessibility-auditor",
    agent_type="atomic",
    description="WCAG 2.2 AA 无障碍审计",
    required_inputs={
        "content": InputSpec(
            type="str",
            description="待审计的 HTML 内容",
            examples=["<html><body><h1>Hello</h1></body></html>"],
        ),
    },
    optional_inputs={
        "content_type": InputSpec(
            type="str",
            description="内容类型",
            examples=["html"],
            required=False,
        ),
    },
    output_schema={
        "issues": OutputSpec(type="list", min_length=0),
        "compliance_score": OutputSpec(type="float"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=50,
        required_keywords=["accessibility"],
        score_threshold=0.6,
    ),
    cli_method="audit",
)

API_DOC_GENERATOR = CapabilityContract(
    agent_name="api-doc-generator",
    agent_type="atomic",
    description="API 文档生成",
    required_inputs={
        "file_path": InputSpec(
            type="str",
            description="API 源码文件路径",
            examples=["agent_api_doc_generator/tools/extract_endpoints.py"],
        ),
    },
    optional_inputs={},
    output_schema={
        "endpoints": OutputSpec(type="list", min_length=0),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=50,
        required_keywords=["endpoint"],
        score_threshold=0.6,
    ),
    cli_method="extract",
)

CONTRACT_ANALYZER = CapabilityContract(
    agent_name="contract-analyzer",
    agent_type="atomic",
    description="合同条款分析",
    required_inputs={
        "text": InputSpec(
            type="str",
            description="合同文本内容",
            examples=["This agreement is between Party A and Party B..."],
        ),
    },
    optional_inputs={},
    output_schema={
        "clauses": OutputSpec(type="list", min_length=0),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=50,
        required_keywords=["clause"],
        score_threshold=0.6,
    ),
    cli_method="extract_clauses",
)

DOC_FILLER = CapabilityContract(
    agent_name="doc-filler",
    agent_type="atomic",
    description="文档模板填充",
    required_inputs={
        "template_path": InputSpec(
            type="str",
            description="文档模板文件路径",
            examples=["SKILL.md"],
        ),
    },
    optional_inputs={
        "values": InputSpec(
            type="dict",
            description="填充数据",
            examples=['{"overview": "A test project"}'],
            required=False,
        ),
    },
    output_schema={
        "output_path": OutputSpec(type="str", min_length=1),
        "success": OutputSpec(type="bool"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=10,
        required_keywords=[],
        score_threshold=0.5,
    ),
    cli_method="fill",
)

LOCALIZATION_SPECIALIST = CapabilityContract(
    agent_name="localization-specialist",
    agent_type="atomic",
    description="本地化翻译专家",
    required_inputs={
        "text": InputSpec(
            type="str",
            description="待本地化文本",
            examples=["Welcome to our platform"],
        ),
        "target_lang": InputSpec(
            type="str",
            description="目标语言",
            examples=["zh-CN"],
        ),
    },
    optional_inputs={},
    output_schema={
        "translated_text": OutputSpec(type="str", min_length=1),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=10,
        required_keywords=[],
        score_threshold=0.6,
    ),
    cli_method="localize",
)

MARKET_INTELLIGENCE = CapabilityContract(
    agent_name="market-intelligence-analyst",
    agent_type="atomic",
    description="市场情报分析",
    required_inputs={
        "data": InputSpec(
            type="str",
            description="分析数据或主题",
            examples=["AI Agent market trend analysis"],
        ),
    },
    optional_inputs={},
    output_schema={
        "insights": OutputSpec(type="list", min_length=0),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=80,
        required_keywords=["market"],
        score_threshold=0.6,
    ),
    cli_method="analyze_market",
)

REQUIREMENTS_ANALYZER = CapabilityContract(
    agent_name="requirements-analyzer",
    agent_type="atomic",
    description="需求分析",
    required_inputs={
        "text": InputSpec(
            type="str",
            description="需求描述",
            examples=["The system shall support user login, registration, and password recovery"],
        ),
    },
    optional_inputs={},
    output_schema={
        "gaps": OutputSpec(type="list", min_length=0),
        "key_terms": OutputSpec(type="list", min_length=0),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=50,
        required_keywords=["requirement"],
        score_threshold=0.6,
    ),
    cli_method="analyze",
)

TEST_SUITE_GENERATOR = CapabilityContract(
    agent_name="test-suite-generator",
    agent_type="atomic",
    description="测试套件生成",
    required_inputs={
        "file_path": InputSpec(
            type="str",
            description="待测试代码文件路径",
            examples=["agent_test_suite_generator/agent.py"],
        ),
    },
    optional_inputs={},
    output_schema={
        "units": OutputSpec(type="list", min_length=0),
        "framework": OutputSpec(type="str"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=50,
        required_keywords=["test"],
        score_threshold=0.6,
    ),
    cli_method="analyze_code_for_tests",
)

GOOD_SKILL = CapabilityContract(
    agent_name="good-skill",
    agent_type="atomic",
    description="自动晋升的通用技能 Agent",
    required_inputs={
        "task": InputSpec(
            type="str",
            description="任务描述",
            examples=["Execute a general task"],
        ),
    },
    optional_inputs={
        "context": InputSpec(
            type="dict",
            description="上下文",
            examples=['{"key": "value"}'],
            required=False,
        ),
    },
    output_schema={
        "output": OutputSpec(type="str", min_length=1),
    },
    output_format="json",
    quality_thresholds=QualityThresholds(
        min_output_length=10,
        required_keywords=[],
        score_threshold=0.5,
    ),
    cli_method="run",
)

ALL_ATOMIC_CONTRACTS: list[CapabilityContract] = [
    SECURITY_SCANNER,
    CODE_REVIEWER,
    ACCESSIBILITY_AUDITOR,
    API_DOC_GENERATOR,
    CONTRACT_ANALYZER,
    DOC_FILLER,
    LOCALIZATION_SPECIALIST,
    MARKET_INTELLIGENCE,
    REQUIREMENTS_ANALYZER,
    TEST_SUITE_GENERATOR,
    GOOD_SKILL,
]

KEY_ATOMIC_CONTRACTS: list[CapabilityContract] = [
    SECURITY_SCANNER,
    CODE_REVIEWER,
    ACCESSIBILITY_AUDITOR,
    REQUIREMENTS_ANALYZER,
]
