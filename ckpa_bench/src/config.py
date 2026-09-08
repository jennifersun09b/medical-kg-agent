"""CKPA-Bench LLM-Forge Pipeline Configuration."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass(frozen=True)
class APIConfig:
    """DeepSeek API configuration."""
    api_key: str = field(default_factory=lambda: os.environ.get("CKPA_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", ""))
    base_url: str = field(default_factory=lambda: os.environ.get("CKPA_API_BASE_URL", "https://api.deepseek.com"))
    model: str = field(default_factory=lambda: os.environ.get("CKPA_API_MODEL", "deepseek-chat"))
    max_tokens: int = 4096
    temperature: float = 0.3
    max_retries: int = 3
    retry_delay: float = 2.0


@dataclass(frozen=True)
class PathConfig:
    """Data and output paths."""
    project_root: Path = field(default_factory=lambda: Path(__file__).parent.parent)

    @property
    def dxy_guidelines(self) -> Path:
        return self.project_root / "专业知识/四高语料/指南/DXY临床指南.jsonl"

    @property
    def dxy_decisions(self) -> Path:
        return self.project_root / "专业知识/四高语料/指南/DXY临床决策.jsonl"

    @property
    def dxy_drugs(self) -> Path:
        return self.project_root / "专业知识/四高语料/药品/DXY药品.jsonl"

    @property
    def drug_csv(self) -> Path:
        return self.project_root / "专业知识/药品细节.csv"

    @property
    def kg_triples(self) -> Path:
        return self.project_root / "专业知识/医药文献知识图谱.txt"

    @property
    def output_dir(self) -> Path:
        import os
        return self.project_root / os.environ.get("CKPA_OUTPUT_DIR", "output")


@dataclass(frozen=True)
class PipelineConfig:
    """Pipeline execution parameters."""
    api: APIConfig = field(default_factory=APIConfig)
    paths: PathConfig = field(default_factory=PathConfig)

    # Core layer
    core_seed_diseases: List[str] = field(default_factory=lambda: [
        "高血压", "糖尿病", "高脂血症", "高尿酸血症",
    ])
    core_target_items: int = 600

    # Drug-label layer
    drug_label_target_items: int = 400
    drug_label_min_field_coverage: float = 0.5  # skip fields with <50% non-empty

    # Cross-source layer
    cross_source_target_items: int = 250

    # Quality control
    consensus_threshold: int = 2  # out of 3 passes
    validation_sample_pct: float = 0.10

    # Batch sizes for API calls
    batch_size: int = 5
    parallel_workers: int = 3
